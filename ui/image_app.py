#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""NeoForgeSD · 生图（独立应用）

为什么单独一个文件：出图只依赖标准库 + SD WebUI HTTP API，不引入任何无关依赖。

能力：
  · 模型切换（/sdapi/v1/sd-models 全量）
  · 尺寸预设（含竖屏）+ 自定义
  · 采样器 / 调度器分开选（本机 Forge 采样器名不带 Karras）
  · 步数 / CFG / seed（-1 随机）+ 复用上一张 seed
  · 1/2/4/8 张串行出图（n_iter，不进 batch_size —— 4G 卡不涨显存）
  · 重绘 img2img（上传图或画布这张 + denoise）
  · 放大 extras（4x-AnimeSharp / 4x-UltraSharp / DAT ...）
  · 历史（点图回填参数）+ 打开产物目录

端口默认 8899。
"""
import base64
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
try:                                    # 提示词中⇄英 / 词库 / 补全（词典来自本机 SD 扩展）
    from core import i18n_tags
    I18N_ERR = ""
except Exception as _e:                 # 词典坏了也不该拖垮出图
    i18n_tags = None
    I18N_ERR = "%s: %s" % (type(_e).__name__, str(_e)[:200])

PORT = int(os.environ.get("NEOFORGE_IMAGE_PORT", os.environ.get("NEOFORGE_CONSOLE_PORT", "8899")))
SD_API = os.environ.get("NEOFORGE_SD_API", "http://127.0.0.1:7860").rstrip("/")
SD_HOME = os.environ.get("NEOFORGE_SD_HOME", r"E:/NeoForge/backend")
SD_PY = os.path.join(SD_HOME, "python", "python.exe")
WORK = os.environ.get("NEOFORGE_IMAGE_WORK", os.environ.get("NEOFORGE_WORK", r"D:/aicg/_neoforge_console"))
os.makedirs(WORK, exist_ok=True)
HISTORY_FILE = os.path.join(WORK, "history_image.json")

JOBS = {}
JOBS_LOCK = threading.Lock()
HIST_LOCK = threading.Lock()


# ---------------------------------------------------------------- SD API
def sd_get(path, timeout=8):
    with urllib.request.urlopen(SD_API + path, timeout=timeout) as r:
        return json.loads(r.read().decode())


def sd_post(path, payload, timeout=3600):
    req = urllib.request.Request(SD_API + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def sd_alive():
    try:
        sd_get("/sdapi/v1/progress", 4)
        return True
    except Exception:
        return False


_MODEL_CACHE = {"t": 0, "data": None}
_STYLE_CACHE = {"t": 0, "data": None}   # SD 的 prompt-styles（中文风格预设）

# 后端生命周期（NeoForgeSD 自己拉起 SD，headless：--nowebui，不用再开 7860 的网页）
START = {"state": "idle", "since": 0.0, "msg": "", "pid": None}
START_LOCK = threading.Lock()

# 低端显卡（<=6GB）实测最优组合（RTX3050 4GB / SD1.5 512~1024）：
#   · 不用 --medvram：medvram 的 block swap 让 1024² 慢 18%，而 4GB 上本就跑得下
#   · 用 --xformers 而不是 --opt-sdp-attention：sdp 在 sm86 上更慢（实测 xformers 快 ~18%）
#   · 默认不开 --no-half-vae：它让 VAE 常驻显存从 2375MB 涨到 3007MB，1024² 因此从 38.6s 拖到 64s。
#     且本机 vae-ft-mse 在 fp16 下输出与 fp32 **逐像素一致**（max_diff=0.0），不存在黑图风险。
# 显存比 4GB 更小可设 NEOFORGE_SD_MEDVRAM=1；遇到黑图/色偏可设 NEOFORGE_SAFE_VAE=1。
SD_ARGS = ["--api", "--nowebui", "--port", "7860",
           "--xformers", "--skip-version-check"]
if os.environ.get("NEOFORGE_SD_MEDVRAM") == "1":
    SD_ARGS.insert(4, "--medvram")
if os.environ.get("NEOFORGE_SAFE_VAE") == "1":
    SD_ARGS.append("--no-half-vae")

# 传给后端每次生成的全局设置：s_min_uncond 只跳负面提示词分支，纯提速、不改画面。
# 刻意不启用 token_merging —— 它会让 1024² 再快 20%，但会合并注意力 token 丢细节，与"精度"相悖。
# 想要极致速度可设 NEOFORGE_TOME=0.3。
_OVERRIDE = {"s_min_uncond": 1.0}
if os.environ.get("NEOFORGE_TOME"):
    try:
        _OVERRIDE["token_merging_ratio"] = float(os.environ["NEOFORGE_TOME"])
    except ValueError:
        pass


def sd_catalog(force=False):
    """模型 / LoRA / 采样器 / 调度器 / 放大模型 / 全局选项 —— 缓存 60s。"""
    if not force and _MODEL_CACHE["data"] and time.time() - _MODEL_CACHE["t"] < 60:
        return _MODEL_CACHE["data"]
    data = {"models": [], "samplers": [], "schedulers": [], "upscalers": [], "loras": [],
            "vaes": [], "opts": {}}
    try:
        data["models"] = [m["title"] for m in sd_get("/sdapi/v1/sd-models", 20)]
        data["samplers"] = [s["name"] for s in sd_get("/sdapi/v1/samplers", 20)]
        data["schedulers"] = [s["name"] for s in sd_get("/sdapi/v1/schedulers", 20)]
        data["upscalers"] = [u["name"] for u in sd_get("/sdapi/v1/upscalers", 20)
                             if u["name"] not in ("None", "Lanczos", "Nearest")]
        data["loras"] = [l["name"] for l in sd_get("/sdapi/v1/loras", 20)]
        data["vaes"] = [v["model_name"] for v in sd_get("/sdapi/v1/sd-vae", 20)]
        o = sd_get("/sdapi/v1/options", 20)
        data["opts"] = {"clip_skip": o.get("CLIP_stop_at_last_layers", 1),
                        "model": o.get("sd_model_checkpoint"),
                        "vae": o.get("sd_vae")}
    except Exception:
        pass
    _MODEL_CACHE.update(t=time.time(), data=data)
    return data


def sd_status():
    online = sd_alive()
    st = {"online": online, "api": SD_API, "sd_home": SD_HOME, "work": WORK,
          "port": PORT, "model": None, "models": [], "samplers": [], "schedulers": [],
          "upscalers": [], "loras": [], "opts": {}, "backend": dict(START)}
    if START["state"] == "starting":
        st["backend"]["elapsed"] = round(time.time() - START["since"], 1)
    if online:
        cat = sd_catalog()
        st.update(cat)
        st["model"] = (cat.get("opts") or {}).get("model") or st.get("model")
        if START["state"] != "starting":
            START.update(state="ready", msg="已在线")
    return st


def _boot_backend():
    # expandable_segments：PyTorch 2.1+ 的显存分配器选项，专治 4GB 卡"明明够却 OOM"的碎片问题。
    # 必须在子进程 CUDA 初始化前注入，所以走 env 而不是启动后再设。
    env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", PYTHONUTF8="1",
               PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True")
    log = os.path.join(WORK, "sd_server.log")
    f = open(log, "ab")
    proc = None
    try:
        proc = subprocess.Popen([SD_PY, "webui.py"] + SD_ARGS, cwd=SD_HOME, env=env,
                                stdout=f, stderr=f,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        START["pid"] = proc.pid
    except Exception as e:
        START.update(state="failed", msg=f"拉起失败：{e}")
        return
    for _ in range(240):  # 最多 8 分钟（首次加载模型很慢）
        time.sleep(2)
        if sd_alive():
            _MODEL_CACHE.update(t=0, data=None)
            START.update(state="ready", msg="已启动")
            return
        if proc.poll() is not None:
            START.update(state="failed", msg=f"进程退出（码 {proc.returncode}），日志：{log}")
            return
    START.update(state="failed", msg=f"启动超时，日志：{log}")


def start_sd():
    """不阻塞：立刻返回，页面轮询 /api/status 看进度。"""
    if sd_alive():
        START.update(state="ready", msg="已经在跑")
        return True, "后端已经在跑"
    if not os.path.isfile(SD_PY):
        START.update(state="failed", msg=f"找不到 python：{SD_PY}")
        return False, f"找不到 python：{SD_PY}"
    with START_LOCK:
        if START["state"] == "starting":
            return True, "已经在启动中"
        START.update(state="starting", since=time.time(), msg="启动中")
    threading.Thread(target=_boot_backend, daemon=True).start()
    return True, "开始启动，约 60–120 秒（首次加载模型更久）"


def stop_sd():
    """结束后端、释放显存。只杀监听 7860 的进程或本服务拉起的那个。"""
    pids = set()
    if START.get("pid"):
        pids.add(int(START["pid"]))
    try:
        raw = subprocess.run(["netstat", "-ano"], capture_output=True, timeout=20).stdout
        out = raw.decode("gbk", "replace")  # netstat 在本机是 GBK；text=True 会被 PYTHONUTF8 带偏
        for line in out.splitlines():
            if ":7860" in line and "LISTENING" in line.upper():
                pid = line.split()[-1].strip()
                if pid.isdigit():
                    pids.add(int(pid))
    except Exception:
        pass
    killed = []
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"],
                           capture_output=True, timeout=20)
            killed.append(pid)
        except Exception:
            pass
    START.update(state="idle", pid=None, msg="已停止")
    _MODEL_CACHE.update(t=0, data=None)
    if killed:
        return True, "已结束进程 " + ", ".join(str(k) for k in killed)
    return False, "没找到可结束的进程"


# ---------------------------------------------------------------- 历史
def history_load():
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            items = json.load(f)
        return items if isinstance(items, list) else []
    except Exception:
        return []


def history_add(rec):
    with HIST_LOCK:
        items = history_load()
        items.insert(0, rec)
        items = items[:300]
        tmp = HISTORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
        os.replace(tmp, HISTORY_FILE)


# ---------------------------------------------------------------- 参数装配
PRESETS = {
    "512x512": (512, 512), "768x768": (768, 768),
    "512x896": (512, 896), "768x1344": (768, 1344),
    "832x1216": (832, 1216), "1216x832": (1216, 832),
}


def _apply_loras(prompt, loras):
    """把 {name, weight} 列表拼成 <lora:name:weight> —— WebUI 原本的写法，前端不用手打。"""
    tags = []
    for item in loras or []:
        name = (item.get("name") or "").strip() if isinstance(item, dict) else str(item).strip()
        if not name:
            continue
        try:
            w = float(item.get("weight", 0.8)) if isinstance(item, dict) else 0.8
        except Exception:
            w = 0.8
        w = max(0.0, min(2.0, w))
        tag = f"<lora:{name}:{w:g}>"
        if tag not in prompt:
            tags.append(tag)
    return (prompt + " " + " ".join(tags)).strip() if tags else prompt


def _common(a):
    w = int(a.get("width") or 512)
    h = int(a.get("height") or 512)
    n = max(1, min(8, int(a.get("n") or 1)))
    prompt = _apply_loras((a.get("prompt") or "").strip(), a.get("loras"))
    return {
        "prompt": prompt,
        "negative_prompt": (a.get("negative") or "").strip(),
        "steps": int(a.get("steps") or 28),
        "cfg_scale": float(a.get("cfg") or 7),
        "width": w, "height": h,
        "sampler_name": a.get("sampler") or "DPM++ 2M",
        "scheduler": a.get("scheduler") or "karras",
        "seed": int(a.get("seed") if a.get("seed") not in (None, "") else -1),
        "batch_size": 1, "n_iter": n,
        "save_images": False, "send_images": True,
        # 低端卡提速：s_min_uncond 跳过接近完成时的负面提示词分支（不改画面）；
        # 该设置只作用于本次请求，不污染 WebUI 的全局 config。
        "override_settings": dict(_OVERRIDE),
    }


def _adetailer(a):
    """ADetailer 面部修复（老 SD 那套也在用 face_yolov8n.pt）—— 走 alwayson_scripts。"""
    ad = a.get("adetailer") or {}
    if not ad.get("enabled"):
        return None
    return {"adetailer": {"args": [True, False, {
        "ad_model": ad.get("model") or "face_yolov8n.pt",
        "ad_confidence": max(0.0, min(1.0, float(ad.get("confidence") or 0.3))),
        "ad_denoising_strength": max(0.0, min(1.0, float(ad.get("denoise") or 0.4))),
        "ad_mask_blur": 4,
        "ad_inpaint_only_masked": True,
        "ad_inpaint_only_masked_padding": 32,
        "ad_use_steps": False, "ad_steps": 28,
        "ad_use_cfg_scale": False, "ad_cfg_scale": 7.0,
        "ad_x_offset": 0, "ad_y_offset": 0,
    }]}}


def build_txt2img(a):
    p = _common(a)
    if not p["prompt"]:
        raise ValueError("提示词不能为空")
    ad = _adetailer(a)
    if ad:
        p["alwayson_scripts"] = ad
    hr = a.get("hires") or {}
    if hr.get("enabled"):
        # 高清修复：低分辨率先出图，再 ×scale 放大重绘一遍（WebUI 的 Hires.fix）
        p.update({
            "enable_hr": True,
            "hr_scale": max(1.0, min(4.0, float(hr.get("scale") or 1.5))),
            "hr_upscaler": hr.get("upscaler") or "Latent",
            "hr_second_pass_steps": int(hr.get("steps") or 0),
            "denoising_strength": max(0.0, min(1.0, float(hr.get("denoise") or 0.4))),
            "hr_resize_x": 0, "hr_resize_y": 0,
        })
    return p


def build_img2img(a):
    p = _common(a)
    src = (a.get("image_b64") or "").split(",")[-1]
    if not src:
        raise ValueError("缺少源图（上传一张，或用画布这张）")
    if (a.get("size_mode") or "keep") == "keep":
        # 不传宽高 = 用源图尺寸（WebUI 的「保持原尺寸」）
        p.pop("width", None)
        p.pop("height", None)
    p.update({"init_images": [src], "denoising_strength": float(a.get("denoise") or 0.45),
              "resize_mode": int(a.get("resize_mode") or 1), "include_init_images": False})
    mask = (a.get("mask_b64") or "").split(",")[-1]
    if mask:
        # 局部重绘：mask 白=重绘；inpainting_fill 1=原图底，2=latent noise
        p.update({
            "mask": mask,
            "mask_blur": int(a.get("mask_blur", 4)),
            "inpainting_fill": int(a.get("inpainting_fill", 1)),
            "inpaint_full_res": bool(a.get("inpaint_full_res", True)),
            "inpaint_full_res_padding": int(a.get("inpaint_full_res_padding", 32)),
            "inpainting_mask_invert": 0,
        })
    ad = _adetailer(a)
    if ad:
        p["alwayson_scripts"] = ad
    return p


def build_upscale(a):
    src = (a.get("image_b64") or "").split(",")[-1]
    if not src:
        raise ValueError("缺少源图（上传一张，或用画布这张）")
    return {"image": src,
            "upscaling_resize": float(a.get("scale") or 2),
            "upscaler_1": a.get("upscaler") or "4x-AnimeSharp",
            "upscaler_2": "None", "upscaler_2_visibility": 0,
            "gfpgan_visibility": max(0.0, min(1.0, float(a.get("gfpgan") or 0))),
            "codeformer_visibility": max(0.0, min(1.0, float(a.get("codeformer") or 0))),
            "codeformer_weight": 0.5}


# ---------------------------------------------------------------- 任务
def job_worker(job, payload, kind):
    t0 = time.time()
    job["state"] = "running"
    try:
        if kind == "upscale":
            r = sd_post("/sdapi/v1/extra-single-image", payload)
            raw = base64.b64decode((r.get("image") or "").split(",")[-1])
            p = os.path.join(WORK, f"{job['id']}_up.png")
            with open(p, "wb") as f:
                f.write(raw)
            job["files"] = [p]
        else:
            path = "/sdapi/v1/txt2img" if kind == "image" else "/sdapi/v1/img2img"
            resp = sd_post(path, payload)
            want = int(payload.get("n_iter") or 1)
            files = []
            for i, b in enumerate((resp.get("images") or [])[:want]):
                p = os.path.join(WORK, f"{job['id']}_{i:02d}.png")
                with open(p, "wb") as f:
                    f.write(base64.b64decode(b.split(",")[-1]))
                files.append(p)
            job["files"] = files
            job["seed"] = _seed_of(resp, payload)
        job["image_urls"] = ["/files/" + os.path.basename(p) for p in job.get("files") or []]
        job["elapsed"] = round(time.time() - t0, 1)
        job["state"] = "done"
        try:
            history_add({
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind,
                "files": [os.path.basename(p) for p in job.get("files") or []],
                "prompt": payload.get("prompt") or "", "negative": payload.get("negative_prompt") or "",
                "width": payload.get("width"), "height": payload.get("height"),
                "steps": payload.get("steps"), "cfg": payload.get("cfg_scale"),
                "sampler": payload.get("sampler_name"), "scheduler": payload.get("scheduler"),
                "seed": job.get("seed"), "denoise": payload.get("denoising_strength"),
                "elapsed": job["elapsed"],
                "params": {k: v for k, v in payload.items()
                           if k in ("prompt", "negative_prompt", "width", "height", "steps",
                                    "cfg_scale", "sampler_name", "scheduler", "denoising_strength")},
            })
        except Exception as e:
            job["history_error"] = str(e)[:160]
    except Exception as e:
        job["state"] = "error"
        job["error"] = f"{type(e).__name__}: {str(e)[:400]}"
        job["elapsed"] = round(time.time() - t0, 1)


def _seed_of(resp, payload):
    try:
        return json.loads(resp.get("info") or "{}").get("seed", payload.get("seed"))
    except Exception:
        return payload.get("seed")


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "NeoForgeImage/1.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _file(self, p):
        if not os.path.isfile(p):
            self._json(404, {"error": "no file"})
            return
        ctype = mimetypes.guess_type(p)[0] or "application/octet-stream"
        with open(p, "rb") as f:
            self._send(200, f.read(), ctype)

    def do_GET(self):
        if self.path.split("?")[0] in ("/", "/index.html"):
            self._send(200, load_page().encode("utf-8"), "text/html; charset=utf-8")
            return
        if self.path.startswith("/api/status"):
            self._json(200, sd_status())
            return
        if self.path.startswith("/api/catalog"):
            self._json(200, sd_catalog(force="force" in self.path))
            return
        if self.path.startswith("/api/i18n"):
            if i18n_tags is None:
                self._json(503, {"error": "提示词词典不可用：" + I18N_ERR})
                return
            try:
                self._json(200, i18n_tags.stats())
            except Exception as e:
                self._json(500, {"error": "%s: %s" % (type(e).__name__, str(e)[:200])})
            return
        if self.path.startswith("/api/suggest"):
            if i18n_tags is None:
                self._json(503, {"error": "提示词词典不可用"})
                return
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            q = (qs.get("q") or [""])[0]
            try:
                limit = int((qs.get("limit") or ["15"])[0])
            except ValueError:
                limit = 15
            # 词典首次加载约 1.5s，前端会先打一次 /api/i18n 预热
            self._json(200, i18n_tags.complete(q, limit))
            return
        if self.path.startswith("/api/promptlib"):
            if i18n_tags is None:
                self._json(503, {"error": "提示词词典不可用"})
                return
            self._json(200, i18n_tags.promptlib())
            return
        if self.path.startswith("/api/styles"):
            if not sd_alive():
                self._json(200, {"items": [], "online": False})
                return
            try:
                if not _STYLE_CACHE["data"] or time.time() - _STYLE_CACHE["t"] > 300:
                    _STYLE_CACHE["data"] = sd_get("/sdapi/v1/prompt-styles", 20)
                    _STYLE_CACHE["t"] = time.time()
                self._json(200, {"items": _STYLE_CACHE["data"], "online": True})
            except Exception as e:
                self._json(200, {"items": [], "online": True, "error": str(e)[:200]})
            return
        if self.path.startswith("/api/history"):
            try:
                limit = int(self.path.split("limit=")[1].split("&")[0]) if "limit=" in self.path else 24
            except Exception:
                limit = 24
            items = []
            for rec in history_load()[:max(1, min(300, limit))]:
                r = dict(rec)
                r["urls"] = ["/files/" + f for f in rec.get("files") or []]
                items.append(r)
            self._json(200, {"items": items})
            return
        if self.path.startswith("/api/progress"):
            try:
                self._json(200, sd_get("/sdapi/v1/progress"))
            except Exception as e:
                self._json(200, {"progress": 0, "online": False, "error": str(e)[:200]})
            return
        m = re.match(r"^/api/job/([0-9a-f]+)$", self.path)
        if m:
            with JOBS_LOCK:
                job = JOBS.get(m.group(1))
                if not job:
                    self._json(404, {"error": "no such job"})
                    return
                out = dict(job)
            self._json(200, out)
            return
        if self.path.startswith("/files/"):
            name = os.path.basename(urllib.request.unquote(self.path[7:]))
            self._file(os.path.join(WORK, name))
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        ln = int(self.headers.get("Content-Length") or 0)
        try:
            a = json.loads(self.rfile.read(ln).decode("utf-8")) if ln else {}
        except Exception:
            self._json(400, {"error": "bad json"})
            return

        if self.path == "/api/start_sd":
            ok, msg = start_sd()
            self._json(200, {"ok": ok, "message": msg, "status": sd_status()})
            return

        if self.path == "/api/stop_sd":
            ok, msg = stop_sd()
            self._json(200, {"ok": ok, "message": msg})
            return

        if self.path == "/api/options":
            if not sd_alive():
                self._json(503, {"error": "SD 后端未启动"})
                return
            payload = {}
            if a.get("clip_skip") is not None:
                payload["CLIP_stop_at_last_layers"] = max(1, min(12, int(a["clip_skip"])))
            if not payload:
                self._json(400, {"error": "没有可设置的选项"})
                return
            try:
                sd_post("/sdapi/v1/options", payload, timeout=60)
                _MODEL_CACHE.update(t=0, data=None)
                self._json(200, {"ok": True, "opts": sd_catalog(force=True).get("opts")})
            except Exception as e:
                self._json(500, {"error": f"{type(e).__name__}: {str(e)[:300]}"})
            return

        if self.path == "/api/vae":
            if not sd_alive():
                self._json(503, {"error": "SD 后端未启动"})
                return
            name = (a.get("name") or "").strip()
            try:
                sd_post("/sdapi/v1/options", {"sd_vae": name}, timeout=300)
                _MODEL_CACHE.update(t=0, data=None)
                self._json(200, {"ok": True, "opts": sd_catalog(force=True).get("opts")})
            except Exception as e:
                self._json(500, {"error": f"{type(e).__name__}: {str(e)[:300]}"})
            return

        if self.path == "/api/pnginfo":
            if not sd_alive():
                self._json(503, {"error": "SD 后端未启动"})
                return
            img = a.get("image_b64") or ""
            if not img:
                self._json(400, {"error": "缺少 image_b64"})
                return
            try:
                r = sd_post("/sdapi/v1/png-info", {"image": img}, timeout=120)
                self._json(200, {"ok": True, "info": r.get("info") or ""})
            except Exception as e:
                self._json(500, {"error": f"{type(e).__name__}: {str(e)[:300]}"})
            return

        if self.path == "/api/translate":
            if i18n_tags is None:
                self._json(503, {"error": "提示词词典不可用：" + I18N_ERR})
                return
            text = a.get("text") or ""
            direction = a.get("dir") or "zh2en"
            web = bool(a.get("web", True))
            t0 = time.time()
            try:
                if direction == "en2zh":
                    r = i18n_tags.en2zh(text)
                else:
                    r = i18n_tags.zh2en(text, web=web)
                r["elapsed"] = round(time.time() - t0, 2)
                r["dir"] = direction
                self._json(200, r)
            except Exception as e:
                self._json(500, {"error": "%s: %s" % (type(e).__name__, str(e)[:300])})
            return

        if self.path == "/api/open_folder":
            try:
                os.startfile(WORK)  # noqa: S606  (Windows 资源管理器)
                self._json(200, {"ok": True, "folder": WORK})
            except Exception as e:
                self._json(200, {"ok": False, "error": str(e)[:200]})
            return

        if self.path == "/api/model":
            if not sd_alive():
                self._json(503, {"error": "SD 后端未启动"})
                return
            name = (a.get("name") or "").strip()
            if not name:
                self._json(400, {"error": "缺少 name"})
                return
            try:
                sd_post("/sdapi/v1/options", {"sd_model_checkpoint": name}, timeout=600)
                _MODEL_CACHE.update(t=0, data=None)
                self._json(200, {"ok": True, "model": sd_get("/sdapi/v1/options", 20).get("sd_model_checkpoint")})
            except Exception as e:
                self._json(500, {"error": f"{type(e).__name__}: {str(e)[:300]}"})
            return

        if self.path in ("/api/image", "/api/img2img", "/api/upscale"):
            if not sd_alive():
                self._json(503, {"error": "SD 后端未启动，先点『启动后端』"})
                return
            kind = {"image": "image", "img2img": "edit", "upscale": "upscale"}[
                self.path.rsplit("/", 1)[-1]]
            try:
                payload = {"image": build_txt2img, "edit": build_img2img,
                           "upscale": build_upscale}[kind](a)
            except Exception as e:
                self._json(400, {"error": str(e)})
                return
            jid = uuid.uuid4().hex[:12]
            job = {"id": jid, "kind": kind, "state": "queued", "created": time.time(),
                   "params": {k: payload.get(k) for k in
                              ("prompt", "width", "height", "steps", "cfg_scale",
                               "sampler_name", "scheduler", "n_iter", "denoising_strength")}}
            with JOBS_LOCK:
                JOBS[jid] = job
            threading.Thread(target=job_worker, args=(job, payload, kind), daemon=True).start()
            self._json(200, {"job_id": jid})
            return

        self._json(404, {"error": "not found"})


PAGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "image_page.html")


def load_page():
    """页面是独立 HTML 文件：改界面不用动 Python，也不怕转义炸。"""
    try:
        with open(PAGE_FILE, encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return ("<!doctype html><meta charset='utf-8'><h1>页面读取失败</h1>"
                "<pre>%s\n应当存在：%s</pre>" % (e, PAGE_FILE))





def main():
    print(f"NeoForgeSD 生图  → http://127.0.0.1:{PORT}/")
    print(f"后端 SD API: {SD_API}   产物目录: {WORK}")
    print("SD 后端以 --nowebui 启动：没有 WebUI 页面，全部操作在本页")
    st = sd_status()
    print("SD 状态:", "在线" if st["online"] else "离线（页面可点『启动后端』）", "·", st.get("model"))
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
