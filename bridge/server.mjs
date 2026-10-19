#!/usr/bin/env node
/**
 * aigc-bridge — Local MCP server.
 *
 * Bridges THIS machine's AIGC software into Hermes as first-class MCP tools:
 *   - Stable Diffusion WebUI (API on :7860)  -> image gen/edit/upscale, AnimateDiff video gen
 *   - ffmpeg/ffprobe (bundled with LosslessCut) -> video/audio processing
 *   - Blender (CLI)                          -> 3D render / procedural output
 *   - Pillow (python)                        -> offline image batch processing
 *   - edge-tts / GPT-SoVITS (:9880)          -> speech synthesis & voice cloning
 *
 * stdout is reserved for JSON-RPC. All diagnostics go to stderr.
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { spawn } from "node:child_process";
import http from "node:http";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/* ------------------------------------------------------------------ *
 * Configuration — every path is overridable via env so this server
 * survives the user moving/upgrading their AIGC stack.
 * ------------------------------------------------------------------ */
const CFG = {
  comfyUrl: process.env.COMFY_URL || "http://127.0.0.1:8188",
  sdUrl: process.env.SD_WEBUI_URL || "http://127.0.0.1:7860",
  sdRoot: process.env.SD_ROOT || "E:\\sd\\sd-webui-aki-v4.8",
  sdLauncher: process.env.SD_LAUNCHER || "E:\\sd\\启动SD-API.bat",
  sdPython: process.env.SD_PYTHON || path.join(process.env.SD_ROOT || "E:\\sd\\sd-webui-aki-v4.8", "python", "python.exe"),
  // --skip-install: the extension "install requirements" step downgrades ml_dtypes
  //   and breaks startup (the classic "no attribute float8_e3m4" crash).
  // 2026-10-16 修复：--opt-sdp-attention 被移除。
  //   NeoForge WebUI（Forge Classic）的 launch.py 直接报 "unrecognized arguments: --opt-sdp-attention"，
  //   导致 sd_start 永远起不来（SD 从未真正启动）。且它已无必要：
  //   xformers 0.0.25.post1+cu118 现已装好，启动日志会打印
  //   "xformers version: 0.0.25.post1+cu118"，自动生效。
  sdArgs: (process.env.SD_ARGS || "--api --skip-install").split(" ").filter(Boolean),
  ffmpeg: process.env.FFMPEG_BIN || "E:\\LosslessCut\\resources\\ffmpeg.exe",
  ffprobe: process.env.FFPROBE_BIN || "E:\\LosslessCut\\resources\\ffprobe.exe",
  blender: process.env.BLENDER_BIN || "D:\\Blender\\blender.exe",
  python: process.env.PYTHON_BIN || "E:\\python\\python.exe",
  gsvUrl: process.env.GSV_URL || "http://127.0.0.1:9880",
  // GSV-TTS-Lite: a GPT-SoVITS inference engine (same /tts v2 API shape, port 9880).
  // Lives on D: after being moved off a half-built C:\Users\...\gsv-env venv.
  gsvPython: process.env.GSV_PYTHON || "D:\\aigc\\gsv-env\\Scripts\\python.exe",
  gsvApi: process.env.GSV_API || "D:\\aigc\\GSV-TTS-Lite\\API\\personal_api.py",
  gsvModels: process.env.GSV_MODELS || "D:\\aigc\\GSV-TTS-Lite\\models",
  gsvPort: Number(process.env.GSV_PORT || 9880),
  outRoot:
    process.env.AIGC_OUT_DIR ||
    path.join(__dirname, "..", "..", "outputs", "aigc"),
};

const PY_HELPER = path.join(__dirname, "py", "pillow_ops.py");

/* ------------------------------------------------------------------ *
 * Small utilities
 * ------------------------------------------------------------------ */
const log = (...a) => console.error("[aigc-bridge]", ...a);

function stamp() {
  const d = new Date();
  const p = (n, w = 2) => String(n).padStart(w, "0");
  return (
    `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}` +
    `_${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`
  );
}

async function newRunDir(tag = "run") {
  const dir = path.join(CFG.outRoot, `${stamp()}_${tag}`);
  await fsp.mkdir(dir, { recursive: true });
  return dir;
}

/** Run a native binary (no shell) and capture stdout/stderr. */
function run(cmd, args, { timeoutMs = 600000, cwd } = {}) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(cmd, args, { cwd, windowsHide: true });
    } catch (e) {
      return resolve({ code: -1, stdout: "", stderr: String(e) });
    }
    let out = "";
    let err = "";
    const timer = setTimeout(() => {
      try {
        child.kill("SIGKILL");
      } catch {}
    }, timeoutMs);
    child.stdout?.on("data", (d) => (out += d.toString()));
    child.stderr?.on("data", (d) => (err += d.toString()));
    child.on("error", (e) => {
      clearTimeout(timer);
      resolve({ code: -1, stdout: out, stderr: err + "\n" + String(e) });
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      resolve({ code, stdout: out, stderr: err });
    });
  });
}

/**
 * JSON HTTP call.
 *
 * Deliberately uses node:http rather than global fetch: undici enforces a
 * 5-minute headers timeout that an AbortSignal cannot extend, and a single
 * AnimateDiff / high-step generation easily runs longer than that.
 */
function httpJson(url, { method = "GET", body, timeoutMs = 0 } = {}) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const payload = body ? JSON.stringify(body) : null;
    const req = http.request(
      {
        hostname: u.hostname,
        port: u.port || 80,
        path: u.pathname + u.search,
        method,
        // No connection reuse: uvicorn's idle keep-alive timeout races with a
        // pooled socket being picked up again, which surfaces as a bogus
        // "socket hang up" *after* a generation has already finished.
        agent: false,
        headers: payload
          ? { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(payload), Connection: "close" }
          : { Connection: "close" },
      },
      (res) => {
        const chunks = [];
        res.on("data", (c) => chunks.push(c));
        res.on("end", () => {
          const text = Buffer.concat(chunks).toString("utf8");
          if (res.statusCode < 200 || res.statusCode >= 300) {
            return reject(new Error(`HTTP ${res.statusCode} ${url}\n${text.slice(0, 800)}`));
          }
          if (!text) return resolve({});
          try {
            resolve(JSON.parse(text));
          } catch {
            reject(new Error(`Non-JSON response from ${url}: ${text.slice(0, 300)}`));
          }
        });
      }
    );
    req.setTimeout(0); // no socket idle timeout — generation can run for many minutes
    req.on("error", reject);
    if (timeoutMs > 0) {
      const t = setTimeout(() => req.destroy(new Error(`request timed out after ${timeoutMs}ms`)), timeoutMs);
      req.on("close", () => clearTimeout(t));
    }
    if (payload) req.write(payload);
    req.end();
  });
}

/** POST JSON and return the raw body as a Buffer (for audio endpoints). */
function httpPostBinary(url, obj, { timeoutMs = 0 } = {}) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const payload = Buffer.from(JSON.stringify(obj), "utf8");
    const req = http.request(
      {
        hostname: u.hostname,
        port: u.port || 80,
        path: u.pathname + u.search,
        method: "POST",
        agent: false,
        headers: {
          "Content-Type": "application/json",
          "Content-Length": payload.length,
          Connection: "close",
        },
      },
      (res) => {
        const chunks = [];
        res.on("data", (c) => chunks.push(c));
        res.on("end", () => {
          const buf = Buffer.concat(chunks);
          if (res.statusCode < 200 || res.statusCode >= 300) {
            return reject(new Error(`HTTP ${res.statusCode} ${url}\n${buf.toString("utf8").slice(0, 800)}`));
          }
          resolve({ buf, contentType: res.headers["content-type"] || "" });
        });
      }
    );
    req.setTimeout(0); // first synthesis can be slow while models load
    req.on("error", reject);
    if (timeoutMs > 0) {
      const t = setTimeout(() => req.destroy(new Error(`request timed out after ${timeoutMs}ms`)), timeoutMs);
      req.on("close", () => clearTimeout(t));
    }
    req.write(payload);
    req.end();
  });
}

/** GET raw bytes (for ComfyUI /view image download — returns binary PNG, not JSON). */
function httpGetRaw(url, { timeoutMs = 0 } = {}) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const req = http.request(
      { hostname: u.hostname, port: u.port || 80, path: u.pathname + u.search, method: "GET", agent: false },
      (res) => {
        const chunks = [];
        res.on("data", (c) => chunks.push(c));
        res.on("end", () => {
          if (res.statusCode < 200 || res.statusCode >= 300)
            return reject(new Error(`HTTP ${res.statusCode} ${url}`));
          resolve(Buffer.concat(chunks));
        });
      }
    );
    req.setTimeout(0);
    req.on("error", reject);
    if (timeoutMs > 0) {
      const t = setTimeout(() => req.destroy(new Error(`timeout ${timeoutMs}ms`)), timeoutMs);
      req.on("close", () => clearTimeout(t));
    }
    req.end();
  });
}

async function sdAlive(timeoutMs = 3000) {
  try {
    await httpJson(`${CFG.sdUrl}/sdapi/v1/options`, { timeoutMs });
    return true;
  } catch {
    return false;
  }
}

async function requireSd() {
  if (await sdAlive()) return null;
  return (
    `Stable Diffusion WebUI is not responding at ${CFG.sdUrl}.\n` +
    `Start it first with the sd_start tool (or double-click ${CFG.sdLauncher}).`
  );
}

function ok(text, extra = {}) {
  return { content: [{ type: "text", text }, ...(extra.images || [])] };
}

async function writeB64(b64, dest) {
  await fsp.mkdir(path.dirname(dest), { recursive: true });
  await fsp.writeFile(dest, Buffer.from(b64, "base64"));
  return dest;
}

/**
 * Sniff a base64 payload's real container from its magic bytes.
 * AnimateDiff returns several formats in one list, in code order rather than
 * the order we asked for, so classify instead of trusting position.
 */
function sniffB64(b64) {
  const head = b64.slice(0, 24);
  if (head.startsWith("R0lGOD")) return { ext: "gif", mime: "image/gif" };          // GIF8
  if (head.startsWith("iVBORw0")) return { ext: "png", mime: "image/png" };         // PNG
  if (head.startsWith("UklGR")) return { ext: "webp", mime: "image/webp" };         // RIFF/WEBP
  if (head.startsWith("GkXfow")) return { ext: "webm", mime: "video/webm" };        // EBML
  if (head.startsWith("AAAA") || /^[A-Za-z0-9+/]{4}(.{8})/.test(head)) {
    const probe = Buffer.from(b64.slice(0, 64), "base64").toString("latin1");
    if (probe.includes("ftyp")) return { ext: "mp4", mime: "video/mp4" };
  }
  return { ext: "bin", mime: "application/octet-stream" };
}

/* ------------------------------------------------------------------ *
 * ffmpeg argument builders (named ops — predictable, no shell)
 * ------------------------------------------------------------------ */
function ffArgs(op, a = {}) {
  const vargs = a.videoArgs || ["-c:v", "libx264", "-preset", a.preset || "medium", "-crf", String(a.crf ?? 20)];
  const aargs = a.audioArgs || ["-c:a", "aac", "-b:a", a.audioBitrate || "192k"];
  switch (op) {
    case "cut": {
      const pre = [];
      if (a.fast !== false && a.reencode !== true) pre.push("-ss", String(a.start ?? 0));
      const out = [...pre];
      if (a.reencode === true || a.fast === false) {
        out.push("-i", a.input, "-ss", String(a.start ?? 0));
        if (a.end != null) out.push("-to", String(a.end));
        out.push(...vargs, ...aargs);
      } else {
        out.push("-i", a.input, "-t", String((a.end ?? 0) - (a.start ?? 0)), "-c", "copy");
      }
      return [...out, "-y", a.output];
    }
    case "concat":
      return ["-f", "concat", "-safe", "0", "-i", a.listFile, "-c", "copy", "-y", a.output];
    case "convert":
      return ["-i", a.input, ...(a.args || ["-c:v", "libx264", "-crf", "20", "-c:a", "aac"]), "-y", a.output];
    case "compress": {
      const crf = String(a.crf ?? 26);
      const extra = [];
      if (a.maxWidth) extra.push("-vf", `scale='min(${a.maxWidth},iw)':-2`);
      return ["-i", a.input, "-c:v", "libx264", "-preset", a.preset || "slow", "-crf", crf,
        "-c:a", "aac", "-b:a", a.audioBitrate || "128k", ...extra, "-movflags", "+faststart", "-y", a.output];
    }
    case "gif": {
      const fps = a.fps ?? 12;
      const w = Math.min(a.width ?? 480, 640);
      // Cap duration: an uncapped GIF of a long clip balloons to tens of MB.
      const dur = a.duration ?? 10;
      return [
        "-t", String(dur),
        "-i", a.input,
        "-vf", `fps=${fps},scale=${w}:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse`,
        "-loop", String(a.loop ?? 0), "-y", a.output,
      ];
    }
    case "frames": {
      let out = a.output;
      if (!String(out).includes("%")) {
        const e = path.extname(out);
        out = out.slice(0, out.length - e.length) + "_%04d" + e;
      }
      const base = ["-i", a.input];
      if (a.time != null) base.unshift("-ss", String(a.time));
      if (a.fps) base.push("-vf", `fps=${a.fps}`);
      return [...base, ...(a.time != null ? ["-frames:v", "1"] : []), "-y", out];
    }
    case "audio":
      return ["-i", a.input, "-vn", ...(a.args || ["-c:a", "libmp3lame", "-q:a", "2"]), "-y", a.output];
    case "mute":
      return ["-i", a.input, "-c", "copy", "-an", "-y", a.output];
    case "replace_audio":
      return ["-i", a.input, "-i", a.audio, "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-shortest", "-y", a.output];
    case "speed": {
      const s = a.speed ?? 1;
      return ["-i", a.input,
        "-filter_complex",
        `[0:v]setpts=${(1 / s).toFixed(6)}*PTS[v];[0:a]atempo=${s}[a]`,
        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "20", "-c:a", "aac", "-y", a.output];
    }
    case "resize":
      return ["-i", a.input, "-vf", `scale=${a.width ?? -2}:${a.height ?? 1080}`, ...vargs, ...aargs, "-y", a.output];
    case "thumbnail":
      return ["-ss", String(a.time ?? 0), "-i", a.input, "-frames:v", "1",
        ...(a.width ? ["-vf", `scale=${a.width}:-2`] : []), "-y", a.output];
    case "normalize":
      return ["-i", a.input, "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:v", "copy", "-y", a.output];
    case "watermark": {
      const pos = a.position || "bottomright";
      return ["-i", a.input, "-i", a.image, "-filter_complex",
        `[1:v]scale=${a.wmWidth ?? 160}:-1[wm];[0:v][wm]overlay=${a.x ?? "W-w-24"}:${a.y ?? "H-h-24"}[v]`,
        "-map", "[v]", "-map", "0:a?", ...vargs, ...aargs, "-y", a.output];
    }
    case "subtitles":
      return ["-i", a.input, "-vf",
        `subtitles='${String(a.subs).replace(/\\/g, "/").replace(/:/g, "\\:").replace(/'/g, "\\'")}'`,
        ...vargs, ...aargs, "-y", a.output];
    case "burn_srt_hardsub":
      return ["-i", a.input, "-vf", `ass='${a.subs}'`, ...vargs, ...aargs, "-y", a.output];
    case "custom":
      return [...(a.args || []), "-y", a.output];
    default:
      throw new Error(`unknown ffmpeg op: ${op}`);
  }
}

/* ------------------------------------------------------------------ *
 * Tool definitions
 * ------------------------------------------------------------------ */
const TOOLS = [
  {
    name: "aigc_status",
    description:
      "Health check of the whole local AIGC stack: Stable Diffusion WebUI (API :7860), ffmpeg/ffprobe, Blender, Python/Pillow, edge-tts, GPT-SoVITS (:9880). Run this first when something fails.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
  },
  {
    name: "sd_models",
    description:
      "List Stable Diffusion checkpoints, LoRAs, samplers and the currently loaded model. Use before image_generate to pick a model.",
    inputSchema: {
      type: "object",
      properties: {
        include_loras: { type: "boolean", description: "Also list LoRAs (default true)" },
      },
      additionalProperties: false,
    },
  },
  {
    name: "sd_set_model",
    description: "Switch the active Stable Diffusion checkpoint by (partial) title match.",
    inputSchema: {
      type: "object",
      properties: {
        title: { type: "string", description: "Partial checkpoint title, e.g. 'juggernautXL' or 'Chilloutmix'" },
      },
      required: ["title"],
      additionalProperties: false,
    },
  },
  {
    name: "image_generate",
    description:
      "Text-to-image via the local Stable Diffusion WebUI. Returns the absolute path(s) of the saved PNG(s). Supports LoRA tags in the prompt.",
    inputSchema: {
      type: "object",
      properties: {
        prompt: { type: "string", description: "Positive prompt. LoRA syntax <lora:name:0.8> works." },
        negative_prompt: { type: "string", default: "lowres, bad anatomy, worst quality, watermark, text" },
        width: { type: "integer", default: 512 },
        height: { type: "integer", default: 512 },
        steps: { type: "integer", default: 24 },
        cfg_scale: { type: "number", default: 7 },
        sampler_name: { type: "string", default: "DPM++ 2M" },
        scheduler: { type: "string", default: "karras", description: "NeoForge scheduler. Sampler name must NOT carry the Karras suffix (that 404s)." },
        seed: { type: "integer", default: -1, description: "-1 = random" },
        batch_size: { type: "integer", default: 1 },
        n_iter: { type: "integer", default: 1 },
        model: { type: "string", description: "Optional checkpoint title to switch to first" },
        out_name: { type: "string", description: "Optional base filename for the output" },
      },
      required: ["prompt"],
      additionalProperties: false,
    },
  },
  {
    name: "image_edit",
    description:
      "Image-to-image via local Stable Diffusion: restyle / repaint an existing image with a prompt. denoising_strength controls how far it drifts (0.2 subtle .. 0.8 heavy).",
    inputSchema: {
      type: "object",
      properties: {
        input: { type: "string", description: "Absolute path of the source image" },
        prompt: { type: "string" },
        negative_prompt: { type: "string", default: "lowres, bad anatomy, worst quality, watermark, text" },
        denoising_strength: { type: "number", default: 0.5 },
        steps: { type: "integer", default: 24 },
        cfg_scale: { type: "number", default: 7 },
        sampler_name: { type: "string", default: "DPM++ 2M" },
        scheduler: { type: "string", default: "karras", description: "NeoForge scheduler." },
        width: { type: "integer", default: 0, description: "0 = keep source size" },
        height: { type: "integer", default: 0 },
        seed: { type: "integer", default: -1 },
        out_name: { type: "string" },
      },
      required: ["input", "prompt"],
      additionalProperties: false,
    },
  },
  {
    name: "image_upscale",
    description: "Upscale/denoise an image with Stable Diffusion's extras (R-ESRGAN etc.). Good for finishing thumbnails or AI frames.",
    inputSchema: {
      type: "object",
      properties: {
        input: { type: "string", description: "Absolute path of the source image" },
        scale: { type: "number", default: 2 },
        upscaler: { type: "string", default: "R-ESRGAN 4x+", description: "e.g. R-ESRGAN 4x+, 4x-UltraSharp, Lanczos" },
        face_restore: { type: "boolean", default: false },
        out_name: { type: "string" },
      },
      required: ["input"],
      additionalProperties: false,
    },
  },
  {
    name: "image_process",
    description:
      "Offline image batch processing with Pillow — no GPU, no SD needed. Ops: resize, crop, cover (16:9/9:16/1:1 封面裁剪), thumbnail, watermark (text/image), convert format, montage (contact sheet), rotate, flip, border. Accepts a glob or a list of files.",
    inputSchema: {
      type: "object",
      properties: {
        inputs: { type: "array", items: { type: "string" }, description: "Absolute file paths" },
        glob: { type: "string", description: "Or a glob pattern, e.g. 'E:/shots/*.png'" },
        op: {
          type: "string",
          enum: ["resize", "crop", "cover", "thumbnail", "watermark", "convert", "montage", "rotate", "flip", "border", "info"],
        },
        width: { type: "integer" },
        height: { type: "integer" },
        ratio: { type: "string", description: "for 'cover': '16:9' | '9:16' | '1:1' | '4:3'" },
        quality: { type: "integer", default: 92 },
        format: { type: "string", description: "for 'convert': png|jpg|webp" },
        text: { type: "string", description: "for 'watermark': text content" },
        image: { type: "string", description: "for 'watermark': watermark image path" },
        position: { type: "string", default: "bottomright" },
        opacity: { type: "integer", default: 80 },
        font_size: { type: "integer", default: 48 },
        columns: { type: "integer", default: 4, description: "for 'montage'" },
        out_dir: { type: "string", description: "Output directory (default: new run folder)" },
        suffix: { type: "string", default: "_out" },
      },
      required: ["op"],
      additionalProperties: false,
    },
  },
  {
    name: "tts_speak",
    description:
      "Text-to-speech to an MP3 file. Uses edge-tts (free, offline-installable, many Chinese voices) or GPT-SoVITS when engine='gptsovits'. For video voice-over.",
    inputSchema: {
      type: "object",
      properties: {
        text: { type: "string" },
        voice: { type: "string", default: "zh-CN-XiaoxiaoNeural", description: "e.g. zh-CN-XiaoxiaoNeural, zh-CN-YunxiNeural, zh-CN-YunyangNeural, en-US-AriaNeural" },
        rate: { type: "string", default: "+0%", description: "e.g. '+10%' / '-5%'" },
        volume: { type: "string", default: "+0%" },
        pitch: { type: "string", default: "+0Hz" },
        engine: { type: "string", enum: ["edge", "gptsovits"], default: "edge" },
        out_name: { type: "string" },
      },
      required: ["text"],
      additionalProperties: false,
    },
  },
  {
    name: "voice_clone",
    description:
      "Voice cloning / custom TTS through the local GSV-TTS-Lite API (a GPT-SoVITS V2/V2Pro/V2ProPlus inference engine on 127.0.0.1:9880). Give a 3-10s reference_audio plus its exact transcript (prompt_text); the returned WAV speaks the target text in that voice. Start the server with gsv_start first.",
    inputSchema: {
      type: "object",
      properties: {
        text: { type: "string", description: "Text to synthesize" },
        text_lang: { type: "string", default: "zh", description: "zh | en | ja | auto" },
        reference_audio: { type: "string", description: "Absolute path of the reference voice audio (3-10s is ideal)" },
        prompt_text: { type: "string", description: "Transcript of the reference audio (exact text spoken in it)" },
        prompt_lang: { type: "string", default: "zh" },
        speed: { type: "number", default: 1.0, description: "Speed factor" },
        out_name: { type: "string" },
      },
      required: ["text", "reference_audio", "prompt_text"],
      additionalProperties: false,
    },
  },
  {
    name: "gsv_start",
    description:
      "Launch the local GSV-TTS-Lite API server (GPT-SoVITS voice cloning backend) on port 9880 from D:\\aigc\\gsv-env, then wait until it answers. The FIRST run auto-downloads the pretrained models (it picks ModelScope or HuggingFace by latency, so it works from China).",
    inputSchema: {
      type: "object",
      properties: {
        wait_sec: { type: "integer", default: 300, description: "How long to wait for readiness (model download can be slow)" },
        port: { type: "integer", default: 9880 },
      },
      additionalProperties: false,
    },
  },
  {
    name: "comfy_generate",
    description:
      "Generate images via local ComfyUI (API :8188). Pass a workflow template (path or inline JSON) + per-node patches; it submits, waits, fetches and saves PNGs. For ComfyUI-only models (e.g. DiT models like Anima) that NeoForge's WebUI cannot load. Typical node IDs (Anima workflow): 11/12 = CLIP text, 19 = KSampler(steps/cfg/sampler/scheduler/seed), 28 = EmptyLatentImage(width/height/batch_size), 44 = UNETLoader, 45 = CLIPLoader.",
    inputSchema: {
      type: "object",
      properties: {
        workflow: { type: "string", description: "Path to a ComfyUI API-format workflow JSON, OR an inline JSON object string." },
        patches: { type: "object", description: "Per-node input overrides: { \"<node_id>\": { \"<input_key>\": <value> } }, e.g. {\"11\":{\"text\":\"...\"},\"19\":{\"steps\":8,\"cfg\":1.5}}." },
        out_name: { type: "string", description: "Base filename for outputs" },
        timeout_sec: { type: "integer", default: 600 },
      },
      required: ["workflow"],
      additionalProperties: false,
    },
  },
  {
    name: "sd_start",
    description:
      "Launch the local Stable Diffusion WebUI with its HTTP API enabled, then wait until it is ready. Takes 1-3 minutes on first launch (model loading).",
    inputSchema: {
      type: "object",
      properties: {
        wait_sec: { type: "integer", default: 240 },
      },
      additionalProperties: false,
    },
  },
];

/* ------------------------------------------------------------------ *
 * Handlers
 * ------------------------------------------------------------------ */
const handlers = {
  async aigc_status() {
    const lines = [];
    const sd = await sdAlive();
    lines.push(`Stable Diffusion WebUI (${CFG.sdUrl}): ${sd ? "READY" : "not running"}`);

    const ff = await run(CFG.ffmpeg, ["-version"], { timeoutMs: 15000 });
    lines.push(`ffmpeg: ${ff.code === 0 ? ff.stdout.split("\n")[0].trim() : "MISSING at " + CFG.ffmpeg}`);
    const fp = await run(CFG.ffprobe, ["-version"], { timeoutMs: 15000 });
    lines.push(`ffprobe: ${fp.code === 0 ? "ok" : "MISSING at " + CFG.ffprobe}`);

    const bl = await run(CFG.blender, ["--version"], { timeoutMs: 30000 });
    lines.push(`Blender: ${bl.code === 0 ? (bl.stdout.split("\n")[0] || "").trim() : "MISSING at " + CFG.blender}`);

    const py = await run(CFG.python, ["-c", "import PIL,sys;print('Pillow',PIL.__version__,'py',sys.version.split()[0])"], { timeoutMs: 30000 });
    lines.push(`Python/Pillow: ${py.code === 0 ? py.stdout.trim() : "MISSING — " + py.stderr.trim().slice(0, 200)}`);

    const etts = await run(CFG.python, ["-c", "import edge_tts;print('edge-tts',getattr(edge_tts,'__version__','?'))"], { timeoutMs: 30000 });
    lines.push(`edge-tts: ${etts.code === 0 ? etts.stdout.trim() : "not installed (run: python -m pip install edge-tts)"}`);

    let gsv = "not running";
    try {
      const r = await httpJson(`${CFG.gsvUrl}/docs`, { timeoutMs: 3000 });
      gsv = "reachable";
    } catch {}
    lines.push(`GPT-SoVITS (${CFG.gsvUrl}): ${gsv}`);
    lines.push(`ffmpeg path: ${CFG.ffmpeg}`);
    lines.push(`Output root: ${CFG.outRoot}`);
    return lines.join("\n");
  },

  async sd_models(a) {
    const bad = await requireSd();
    if (bad) return bad;
    const [models, samplers, opts] = await Promise.all([
      httpJson(`${CFG.sdUrl}/sdapi/v1/sd-models`, { timeoutMs: 20000 }),
      httpJson(`${CFG.sdUrl}/sdapi/v1/samplers`, { timeoutMs: 20000 }),
      httpJson(`${CFG.sdUrl}/sdapi/v1/options`, { timeoutMs: 20000 }),
    ]);
    const out = [];
    out.push(`Current checkpoint: ${opts.sd_model_checkpoint || "?"}`);
    out.push(`\nCheckpoints (${models.length}):`);
    for (const m of models) out.push(`  - ${m.title}`);
    out.push(`\nSamplers: ${(samplers || []).map((s) => s.name).join(", ")}`);
    if (a.include_loras !== false) {
      try {
        const loras = await httpJson(`${CFG.sdUrl}/sdapi/v1/loras`, { timeoutMs: 20000 });
        out.push(`\nLoRAs (${loras.length}):`);
        for (const l of loras) out.push(`  - ${l.name}   <lora:${l.name}:1>`);
      } catch {}
    }
    return out.join("\n");
  },

  async sd_set_model(a) {
    const bad = await requireSd();
    if (bad) return bad;
    const models = await httpJson(`${CFG.sdUrl}/sdapi/v1/sd-models`, { timeoutMs: 20000 });
    const hit = models.find((m) => m.title.toLowerCase().includes(String(a.title).toLowerCase()));
    if (!hit) {
      return `No checkpoint matching '${a.title}'. Available:\n` + models.map((m) => "  - " + m.title).join("\n");
    }
    // 2026-10-16 修复：`sd_model_checkpoint` 只认 **不带目录、不带扩展名** 的基名。
    //   "Illustrious-XL-v0.1"                    -> 200 ✅（读回一致）
    //   "SDXL\Illustrious-XL-v0.1.safetensors"   -> 422 找不到
    //   "Illustrious-XL-v0.1.safetensors"        -> 500 且**静默不切换**（最坑）
    //   旧代码发的正是 hit.title（带目录全名）→ 切模型从来没成功过。
    const base = path.basename(hit.title).replace(/\.(safetensors|ckpt)$/i, "");
    await httpJson(`${CFG.sdUrl}/sdapi/v1/options`, {
      method: "POST",
      body: { sd_model_checkpoint: base },
      timeoutMs: 180000,
    });
    // 读回校验：切模型是异步的，必须确认真的换过去了
    let cur = "";
    for (let i = 0; i < 60; i++) {
      try {
        const opts = await httpJson(`${CFG.sdUrl}/sdapi/v1/options`, { timeoutMs: 20000 });
        cur = opts.sd_model_checkpoint || "";
        if (cur.includes(base)) return `Switched checkpoint to: ${base}`;
      } catch {}
      await new Promise((r) => setTimeout(r, 2000));
    }
    return `Requested '${base}' but the active checkpoint still reads '${cur || "?"}' — switch FAILED.`;
  },

  async image_generate(a) {
    const bad = await requireSd();
    if (bad) return bad;
    if (a.model) await this.sd_set_model({ title: a.model });

    const body = {
      prompt: a.prompt,
      negative_prompt: a.negative_prompt,
      width: a.width, height: a.height,
      steps: a.steps, cfg_scale: a.cfg_scale,
      sampler_name: a.sampler_name, scheduler: a.scheduler,
      seed: a.seed,
      batch_size: a.batch_size, n_iter: a.n_iter,
      save_images: false,
    };
    const t0 = Date.now();
    const r = await httpJson(`${CFG.sdUrl}/sdapi/v1/txt2img`, { method: "POST", body, timeoutMs: 900000 });
    const dir = await newRunDir("img");
    const base = a.out_name || "img";
    const paths = [];
    for (let i = 0; i < (r.images || []).length; i++) {
      const p = path.join(dir, r.images.length === 1 ? `${base}.png` : `${base}_${String(i + 1).padStart(2, "0")}.png`);
      await writeB64(r.images[i], p);
      paths.push(p);
    }
    let meta = {};
    try { meta = JSON.parse(r.info || "{}"); } catch {}
    const secs = ((Date.now() - t0) / 1000).toFixed(1);
    const text =
      `Generated ${paths.length} image(s) in ${secs}s\n` +
      `seed: ${meta.seed ?? "?"}   size: ${a.width}x${a.height}   steps: ${a.steps}   sampler: ${a.sampler_name}\n` +
      `Files:\n` + paths.map((p) => "  " + p).join("\n");
    return { text, paths };
  },

  async image_edit(a) {
    const bad = await requireSd();
    if (bad) return bad;
    const b64 = (await fsp.readFile(a.input)).toString("base64");
    const body = {
      init_images: [b64],
      prompt: a.prompt,
      negative_prompt: a.negative_prompt,
      denoising_strength: a.denoising_strength,
      steps: a.steps, cfg_scale: a.cfg_scale,
      sampler_name: a.sampler_name, scheduler: a.scheduler,
      seed: a.seed,
      save_images: false,
    };
    if (a.width) body.width = a.width;
    if (a.height) body.height = a.height;
    const r = await httpJson(`${CFG.sdUrl}/sdapi/v1/img2img`, { method: "POST", body, timeoutMs: 900000 });
    const dir = await newRunDir("edit");
    const base = a.out_name || path.parse(a.input).name + "_edit";
    const paths = [];
    for (let i = 0; i < (r.images || []).length; i++) {
      const p = path.join(dir, r.images.length === 1 ? `${base}.png` : `${base}_${i + 1}.png`);
      await writeB64(r.images[i], p);
      paths.push(p);
    }
    return { text: `Edited image -> ${paths.length} file(s):\n` + paths.map((p) => "  " + p).join("\n"), paths };
  },

  async image_upscale(a) {
    const bad = await requireSd();
    if (bad) return bad;
    const b64 = (await fsp.readFile(a.input)).toString("base64");
    const body = {
      image: b64,
      upscaling_resize: a.scale,
      upscaler_1: a.upscaler,
      upscaler_2: "None",
      gfpgan_visibility: a.face_restore ? 0.5 : 0,
      codeformer_visibility: 0,
    };
    const r = await httpJson(`${CFG.sdUrl}/sdapi/v1/extra-single-image`, { method: "POST", body, timeoutMs: 900000 });
    const dir = await newRunDir("upscale");
    const p = path.join(dir, (a.out_name || path.parse(a.input).name + "_x" + a.scale) + ".png");
    await writeB64(r.image, p);
    return { text: `Upscaled x${a.scale} with ${a.upscaler} -> ${p}`, paths: [p] };
  },

  async image_process(a) {
    let files = a.inputs ? [...a.inputs] : [];
    if (a.glob) {
      const { globSync } = await import("node:fs");
      const dir = path.dirname(a.glob);
      const pat = path.basename(a.glob);
      if (fs.existsSync(dir)) {
        const re = new RegExp("^" + pat.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".") + "$", "i");
        for (const f of await fsp.readdir(dir)) if (re.test(f)) files.push(path.join(dir, f));
      }
    }
    if (!files.length) return "No input files. Pass 'inputs' (array) or 'glob'.";
    const missing = files.filter((f) => !fs.existsSync(f));
    if (missing.length) return "File(s) not found:\n" + missing.join("\n");

    const outDir = a.out_dir || (await newRunDir("pillow"));
    const spec = {
      op: a.op, inputs: files, out_dir: outDir,
      width: a.width, height: a.height, ratio: a.ratio,
      quality: a.quality, format: a.format,
      text: a.text, image: a.image, position: a.position, opacity: a.opacity,
      font_size: a.font_size, columns: a.columns, suffix: a.suffix,
    };
    const r = await run(CFG.python, [PY_HELPER, JSON.stringify(spec)], { timeoutMs: 600000 });
    if (r.code !== 0) return `image_process failed:\n${r.stderr || r.stdout}`;
    const payload = JSON.parse(r.stdout.trim().split("\n").pop());
    return { text: `image_process '${a.op}' -> ${payload.outputs.length} file(s)\n` + payload.outputs.map((p) => "  " + p).join("\n"), paths: payload.outputs };
  },

  async tts_speak(a) {
    const dir = await newRunDir("tts");
    const out = path.join(dir, (a.out_name || "speech") + ".mp3");
    if (a.engine === "gptsovits") {
      return "engine='gptsovits' needs reference audio — use the voice_clone tool instead.";
    }
    const code = `
import asyncio, sys
import edge_tts
async def main():
    c = edge_tts.Communicate(sys.argv[1], sys.argv[2], rate=sys.argv[3], volume=sys.argv[4], pitch=sys.argv[5])
    await c.save(sys.argv[6])
asyncio.run(main())
`;
    const tmp = path.join(dir, "_tts.py");
    await fsp.writeFile(tmp, code, "utf8");
    const r = await run(CFG.python, [tmp, a.text, a.voice, a.rate, a.volume, a.pitch, out], { timeoutMs: 300000 });
    if (r.code !== 0) {
      return `tts_speak failed. Is edge-tts installed? (python -m pip install edge-tts)\n${(r.stderr || r.stdout).slice(-800)}`;
    }
    if (!fs.existsSync(out)) return `tts_speak produced no file.\n${r.stderr.slice(-500)}`;
    const st = await fsp.stat(out);
    return { text: `tts_speak -> ${out} (${(st.size / 1024).toFixed(0)} KB, voice ${a.voice} ${a.rate})`, paths: [out] };
  },

  async voice_clone(a) {
    if (!fs.existsSync(a.reference_audio)) return `Reference audio not found: ${a.reference_audio}`;
    const dir = await newRunDir("clone");
    const out = path.join(dir, (a.out_name || "clone") + ".wav");
    // GSV-TTS-Lite's POST /tts speaks the official GPT-SoVITS api_v2 shape.
    const body = {
      text: a.text,
      text_lang: a.text_lang,
      ref_audio_path: a.reference_audio,
      prompt_text: a.prompt_text,
      prompt_lang: a.prompt_lang,
      media_type: "wav",
      streaming_mode: false,
      speed_factor: a.speed ?? 1.0,
    };
    try {
      const { buf, contentType } = await httpPostBinary(`${CFG.gsvUrl}/tts`, body);
      await fsp.writeFile(out, buf);
      const riff = buf.slice(0, 4).toString("latin1");
      const note = riff === "RIFF" ? "" : ` (warning: response is ${contentType}, not WAV — first 200 bytes: ${buf.slice(0, 200).toString("utf8")})`;
      return { text: `voice_clone -> ${out} (${(buf.length / 1024).toFixed(0)} KB)${note}`, paths: [out] };
    } catch (e) {
      return (
        `GSV-TTS-Lite API not reachable at ${CFG.gsvUrl}.\n` +
        `Start it with the gsv_start tool first.\n` +
        `Detail: ${e.message}`
      );
    }
  },

  async gsv_start(a) {
    const port = a.port || CFG.gsvPort;
    const url = `http://127.0.0.1:${port}`;
    try {
      await httpJson(`${url}/`, { timeoutMs: 4000 });
      return `Already running at ${url}.`;
    } catch {}

    if (!fs.existsSync(CFG.gsvPython)) {
      return `GSV python not found: ${CFG.gsvPython}\nSet GSV_PYTHON / GSV_API env vars.`;
    }
    if (!fs.existsSync(CFG.gsvApi)) {
      return `GSV API script not found: ${CFG.gsvApi}\nSet GSV_API to the personal_api.py path.`;
    }
    await fsp.mkdir(path.dirname(CFG.gsvApi), { recursive: true });
    const logFile = path.join(path.dirname(CFG.gsvApi), "gsv-api.log");
    const fd = fs.openSync(logFile, "a");
    const child = spawn(
      CFG.gsvPython,
      [CFG.gsvApi, "--port", String(port), "--models_dir", CFG.gsvModels],
      {
        cwd: path.dirname(CFG.gsvApi),
        detached: true,
        stdio: ["ignore", fd, fd],
        windowsHide: true,
      }
    );
    child.unref();

    const waitSec = a.wait_sec || 300;
    const deadline = Date.now() + waitSec * 1000;
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 5000));
      try {
        const root = await httpJson(`${url}/`, { timeoutMs: 4000 });
        return (
          `GSV-TTS-Lite API is up at ${url}\n` +
          `service: ${root.message || "ok"}\n` +
          `voice_clone is now usable (needs reference audio + its transcript).\n` +
          `log: ${logFile}`
        );
      } catch {}
    }
    return (
      `Started but not answering after ${waitSec}s — the first run downloads the pretrained models, which can take a while.\n` +
      `Check the log: ${logFile}\nThen re-run aigc_status.`
    );
  },

  async comfy_generate(a) {
    const alive = async () => {
      try { await httpJson(`${CFG.comfyUrl}/system_stats`, { timeoutMs: 4000 }); return true; } catch { return false; }
    };
    if (!(await alive())) {
      return `ComfyUI is not responding at ${CFG.comfyUrl}. Start it first (double-click D:\\AIGC\\comfyui\\启动ComfyUI.cmd).`;
    }
    let wfRaw = a.workflow;
    if (!String(wfRaw).trim().startsWith("{")) wfRaw = await fsp.readFile(wfRaw, "utf8");
    let workflow;
    try { workflow = JSON.parse(wfRaw); } catch (e) { return `workflow 不是合法 JSON：${e.message}`; }

    for (const [nid, kv] of Object.entries(a.patches || {})) {
      if (!workflow[nid]) workflow[nid] = { inputs: {} };
      if (!workflow[nid].inputs) workflow[nid].inputs = {};
      Object.assign(workflow[nid].inputs, kv);
    }

    const clientId = "hermes-" + Date.now() + "-" + Math.random().toString(36).slice(2);
    const submit = await httpJson(`${CFG.comfyUrl}/prompt`, {
      method: "POST",
      body: { prompt: workflow, client_id: clientId },
      timeoutMs: 30000,
    });
    if (!submit.prompt_id) return `ComfyUI 未返回 prompt_id：${JSON.stringify(submit).slice(0, 300)}`;
    const pid = submit.prompt_id;

    const deadline = Date.now() + (a.timeout_sec || 600) * 1000;
    let entry = null;
    while (Date.now() < deadline) {
      const h = await httpJson(`${CFG.comfyUrl}/history/${pid}`, { timeoutMs: 30000 });
      if (h[pid]) {
        entry = h[pid];
        const st = entry.status || {};
        if (st.status_str === "error" || st.completed === false) {
          return `ComfyUI 执行报错：${JSON.stringify(st.messages || []).slice(0, 600)}`;
        }
        break;
      }
      await new Promise((r) => setTimeout(r, 1500));
    }
    if (!entry) return `等待 ${a.timeout_sec || 600}s 仍未完成（prompt_id=${pid}）`;

    const dir = await newRunDir("comfy");
    const base = a.out_name || "comfy";
    const paths = [];
    let idx = 0;
    for (const node of Object.values(entry.outputs || {})) {
      for (const im of node.images || []) {
        const q = new URLSearchParams({ filename: im.filename, subfolder: im.subfolder || "", type: im.type || "output" });
        const bytes = await httpGetRaw(`${CFG.comfyUrl}/view?${q}`, { timeoutMs: 180000 });
        const p = path.join(dir, idx === 0 ? `${base}.png` : `${base}_${String(idx).padStart(2, "0")}.png`);
        await fsp.writeFile(p, bytes);
        paths.push(p);
        idx++;
      }
    }
    return { text: `ComfyUI 出图 ${paths.length} 张：\n` + paths.map((p) => "  " + p).join("\n"), paths };
  },

  async sd_start(a) {
    if (await sdAlive()) return `Already running at ${CFG.sdUrl}.`;
    await fsp.mkdir(CFG.outRoot, { recursive: true });
    const logFile = path.join(CFG.outRoot, "sd-launch.log");
    const fd = fs.openSync(logFile, "a");

    let child;
    if (fs.existsSync(CFG.sdPython)) {
      // Preferred: launch launch.py ourselves so the attention optimisation and
      // --skip-install flags are guaranteed (the plain launcher .bat lacks them).
      child = spawn(CFG.sdPython, ["launch.py", ...CFG.sdArgs], {
        cwd: CFG.sdRoot,
        detached: true,
        stdio: ["ignore", fd, fd],
        windowsHide: true,
        env: {
          ...process.env,
          HF_HOME: path.join(CFG.sdRoot, ".cache", "huggingface"),
          SSL_CERT_FILE: "",
          HUGGINGFACE_HUB_CACHE: "",
          HF_HUB_CACHE: "",
          REQUESTS_CA_BUNDLE: "",
          CURL_CA_BUNDLE: "",
        },
      });
    } else if (fs.existsSync(CFG.sdLauncher)) {
      child = spawn("cmd.exe", ["/c", CFG.sdLauncher], {
        detached: true,
        stdio: ["ignore", fd, fd],
        windowsHide: true,
        cwd: path.dirname(CFG.sdLauncher),
      });
    } else {
      return `Could not find the webui python at ${CFG.sdPython}\nSet SD_ROOT / SD_PYTHON / SD_LAUNCHER env vars.`;
    }
    child.unref();

    const waitSec = a.wait_sec || 240;
    const deadline = Date.now() + waitSec * 1000;
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 5000));
      if (await sdAlive()) {
        const opts = await httpJson(`${CFG.sdUrl}/sdapi/v1/options`, { timeoutMs: 15000 });
        return (
          `SD WebUI is up at ${CFG.sdUrl}\n` +
          `checkpoint: ${opts.sd_model_checkpoint}\n` +
          `attention: ${opts.cross_attention_optimization}\n` +
          `launch args: ${CFG.sdArgs.join(" ")}\n` +
          `log: ${logFile}`
        );
      }
    }
    return (
      `Launched but not ready after ${waitSec}s — it may still be loading.\n` +
      `Check the log: ${logFile}\nThen re-run aigc_status.`
    );
  },
};

/* ------------------------------------------------------------------ *
 * 分线开关（出图=主线，视频=支线）
 *   NEOFORGE_BRIDGE_LINE=image  只暴露图像链路工具
 *   NEOFORGE_BRIDGE_LINE=video  只暴露视频 / Blender / 语音工具
 *   不设或 all             → 全部（默认，向后兼容）
 * 只影响 tools/list；handlers 本身不删，脚本直调不受影响。
 * ------------------------------------------------------------------ */
const LINE = String(process.env.NEOFORGE_BRIDGE_LINE || "all").toLowerCase();
const LINE_SETS = {
  image: new Set([
    "aigc_status", "sd_start", "sd_models", "sd_set_model",
    "image_generate", "image_edit", "image_upscale", "image_process", "comfy_generate",
  ]),
  video: new Set([
    "aigc_status",
    "tts_speak", "voice_clone", "gsv_start",
  ]),
};
const ACTIVE_TOOLS = LINE_SETS[LINE] ? TOOLS.filter((t) => LINE_SETS[LINE].has(t.name)) : TOOLS;
if (LINE_SETS[LINE]) {
  log(`tool line = ${LINE} · ${ACTIVE_TOOLS.length}/${TOOLS.length} tools`);
} else if (LINE !== "all") {
  log(`unknown NEOFORGE_BRIDGE_LINE='${LINE}' — falling back to all tools`);
}

/* ------------------------------------------------------------------ *
 * Defaults: MCP does not apply JSON-Schema defaults for us, so merge
 * every declared default into the incoming args once, centrally.
 * ------------------------------------------------------------------ */
const DEFAULTS = Object.fromEntries(
  TOOLS.map((t) => [
    t.name,
    Object.fromEntries(
      Object.entries(t.inputSchema.properties || {})
        .filter(([, v]) => v && Object.prototype.hasOwnProperty.call(v, "default"))
        .map(([k, v]) => [k, v.default])
    ),
  ])
);

function withDefaults(name, args) {
  const d = DEFAULTS[name] || {};
  const out = { ...d, ...args };
  // strip explicit null/undefined so `a.x ?? default` behaves as written
  for (const k of Object.keys(out)) if (out[k] === undefined || out[k] === null) delete out[k];
  return out;
}

/* ------------------------------------------------------------------ *
 * Server wiring
 * ------------------------------------------------------------------ */
const server = new Server(
  { name: "aigc-bridge", version: "1.0.0" },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: ACTIVE_TOOLS }));

server.setRequestHandler(CallToolRequestSchema, async (req) => {
  const { name, arguments: rawArgs = {} } = req.params;
  const args = withDefaults(name, rawArgs);
  if (LINE_SETS[LINE] && !LINE_SETS[LINE].has(name)) {
    return {
      content: [{ type: "text", text: `Tool '${name}' 不在当前分线 '${LINE}' 里（设 NEOFORGE_BRIDGE_LINE=all 可全开）` }],
      isError: true,
    };
  }
  const fn = handlers[name];
  if (!fn) {
    return { content: [{ type: "text", text: `Unknown tool: ${name}` }], isError: true };
  }
  log(`-> ${name} ${JSON.stringify(args).slice(0, 300)}`);
  try {
    const res = await fn.call(handlers, args);
    log(`<- ${name} ok`);
    if (typeof res === "string") {
      return { content: [{ type: "text", text: res }] };
    }
    const content = [{ type: "text", text: res.text }];
    // Inline images so the model/user can see results directly — but NEVER inline a
    // large file: a multi-MB base64 blob in a single JSON-RPC line kills the transport.
    const MAX_INLINE = 3 * 1024 * 1024;
    for (const p of res.paths || []) {
      if (!p || !fs.existsSync(p)) continue;
      const ext = path.extname(p).toLowerCase();
      if (![".png", ".jpg", ".jpeg", ".webp", ".gif"].includes(ext)) continue;
      let size = 0;
      try { size = (await fsp.stat(p)).size; } catch { continue; }
      if (size > MAX_INLINE) {
        content.push({
          type: "text",
          text: `(not inlined: ${path.basename(p)} is ${(size / 1048576).toFixed(1)} MB, over the ${MAX_INLINE / 1048576} MB inline limit — read it from the path above)`,
        });
        continue;
      }
      const b64 = (await fsp.readFile(p)).toString("base64");
      const mime = ext === ".jpg" || ext === ".jpeg" ? "image/jpeg" : ext === ".gif" ? "image/gif" : ext === ".webp" ? "image/webp" : "image/png";
      content.push({ type: "image", data: b64, mimeType: mime });
    }
    return { content };
  } catch (e) {
    log("tool error", name, e);
    return { content: [{ type: "text", text: `Tool '${name}' threw: ${e.message}` }], isError: true };
  }
});

const transport = new StdioServerTransport();
await server.connect(transport);
log("aigc-bridge ready");

// --- crash forensics: never die silently ---
process.on("uncaughtException", (e) => {
  log("UNCAUGHT EXCEPTION", e && e.stack ? e.stack : e);
});
process.on("unhandledRejection", (e) => {
  log("UNHANDLED REJECTION", e && e.stack ? e.stack : e);
});
process.on("exit", (code) => log(`exit code ${code}`));
process.stdin.on("end", () => log("stdin END (transport peer closed)"));
process.stdin.on("close", () => log("stdin CLOSE"));
process.stdin.on("error", (e) => log("stdin ERROR", e.message));

