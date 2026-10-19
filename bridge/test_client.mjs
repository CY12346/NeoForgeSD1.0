/**
 * test_client.mjs — drive aigc-bridge over stdio JSON-RPC and exercise real tools.
 * Run:  node test_client.mjs
 */
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));

const transport = new StdioClientTransport({
  command: process.execPath,
  args: [path.join(HERE, "server.mjs")],
  stderr: "pipe",
});
const client = new Client({ name: "test", version: "1.0.0" }, { capabilities: {} });

// forward the server's stderr so crashes are visible
transport.stderr?.on("data", (d) => process.stderr.write("[server] " + d.toString()));

const t0 = Date.now();
await client.connect(transport);
console.log(`connected in ${Date.now() - t0}ms\n`);

const { tools } = await client.listTools();
console.log(`TOOLS DISCOVERED: ${tools.length}`);
for (const t of tools) console.log(`  - ${t.name}`);
console.log();

async function call(name, args = {}, showImage = false) {
  const t = Date.now();
  try {
    const r = await client.callTool({ name, arguments: args }, undefined, { timeout: 3600000 });
    const secs = ((Date.now() - t) / 1000).toFixed(1);
    const texts = (r.content || []).filter((c) => c.type === "text").map((c) => c.text).join("\n");
    const imgs = (r.content || []).filter((c) => c.type === "image").length;
    console.log(`\n=== ${name} (${secs}s)${r.isError ? " [isError]" : ""} ===`);
    console.log(texts.slice(0, showImage ? 2500 : 600));
    if (imgs) console.log(`  [${imgs} inline image(s) attached]`);
    return r;
  } catch (e) {
    console.log(`\n=== ${name} THREW ===\n${e.message}`);
    return null;
  }
}

// ---------------------------------------------------------------- tests
const PICK = process.argv.slice(2);
const want = (n) => PICK.length === 0 || PICK.includes(n);

if (want("status")) await call("aigc_status");

if (want("imgproc")) {
  const src = "C:/Users/咸鱼一条/Desktop/634a0573536b39a4a03539d6b6cbcdfb.png";
  if (fs.existsSync(src)) {
    console.log(`\n--- source image: ${src} ---`);
    await call("image_process", { op: "info", inputs: [src] });
    await call("image_process", { op: "cover", inputs: [src], ratio: "16:9", width: 1280, height: 720, suffix: "_thumb" });
    await call("image_process", { op: "watermark", inputs: [src], text: "NeoForgeSD", position: "bottomright", font_size: 42, suffix: "_wm" });
    await call("image_process", { op: "convert", inputs: [src], format: "jpg", quality: 85 });
  } else {
    console.log(`\n[sink] source image missing: ${src}`);
  }
}

if (want("tts")) {
  await call("tts_speak", { text: "自动化测试：本机语音合成通道已打通。", voice: "zh-CN-XiaoxiaoNeural", rate: "+5%" });
}

if (want("sd")) {
  await call("sd_models", { include_loras: false });
  await call("image_generate", {
    prompt: "1girl, silver hair, cherry blossom park, cinematic lighting, masterpiece, best quality",
    negative_prompt: "lowres, bad anatomy, worst quality, watermark, text, extra fingers",
    width: 512, height: 512, steps: 15, cfg_scale: 7, out_name: "test_girl",
  });
  await call("image_generate", {
    prompt: "a red apple on a rustic wooden table, studio softbox light, product shot, 8k",
    width: 512, height: 512, steps: 12, out_name: "test_apple",
  });
}

if (want("edit")) {
  await call("image_edit", {
    input: "C:/Users/咸鱼一条/Desktop/634a0573536b39a4a03539d6b6cbcdfb.png",
    prompt: "oil painting style, vivid colors, textured brush strokes",
    denoising_strength: 0.45, steps: 15, out_name: "test_stylize",
  });
  await call("image_upscale", {
    input: "C:/Users/咸鱼一条/Desktop/634a0573536b39a4a03539d6b6cbcdfb.png",
    scale: 2, upscaler: "R-ESRGAN 4x+", out_name: "test_upscale",
  });
}

if (want("clone")) {
  await call("voice_clone", {
    text: "盛暑的阳光斜斜切进花店，落在货架的花盆上。韩星放下喷壶，抬眼看向门口。",
    text_lang: "zh",
    reference_audio: "D:/aigc/GSV-TTS-Lite/refs/ref_xiaoxiao.wav",
    prompt_text: "今天的风很温柔，花店里的花都开了，你要不要进来看看。",
    prompt_lang: "zh",
    out_name: "mcp_clone",
  });
}

await client.close();
console.log("\nDONE");
process.exit(0);
