#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""NeoForgeSD 本地控制台（零依赖，只用标准库）

作用：给 NeoForgeSD 一个能点进去的图形入口。
  - 出图（txt2img）→ 立绘
  - 动作（img2img + AnimateDiff）→ 图生视频，MP4 在线播放 / 下载
  - 状态灯：SD1.5 API 是否在线、当前底模、AnimateDiff 模型

启动：python ui/console.py  → http://127.0.0.1:8899
后端：NeoForgeSD 的 SD WebUI 兼容层（默认 http://127.0.0.1:7860）
"""
import base64
import io
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
try:
    from core import motion as motion_engine
    from core import quality as quality_check
    MOTION_OK = True
except Exception as _e:  # 缺 numpy/PIL 时不拖垮出图功能
    MOTION_OK = False
    MOTION_ERR = str(_e)

PORT = int(os.environ.get("NEOFORGE_CONSOLE_PORT", "8899"))
SD_API = os.environ.get("NEOFORGE_SD_API", "http://127.0.0.1:7860").rstrip("/")
SD_HOME = os.environ.get("NEOFORGE_SD_HOME", r"E:/sd/sd-old-aki")
SD_PY = os.path.join(SD_HOME, "python", "python.exe")
AD_OUTPUT = os.path.join(SD_HOME, "outputs", "img2img-images", "AnimateDiff")
WORK = os.environ.get("NEOFORGE_WORK", r"D:/aicg/_neoforge_console")
os.makedirs(WORK, exist_ok=True)
CORN_BG = os.environ.get("NEOFORGE_CORN_BG",
                         os.path.join(REPO, "assets", "bg_cornfield.png"))

JOBS = {}
JOBS_LOCK = threading.Lock()


# ---------------------------------------------------------------- SD API
def sd_get(path, timeout=8):
    with urllib.request.urlopen(SD_API + path, timeout=timeout) as r:
        return json.loads(r.read().decode())


def sd_post(path, payload, timeout=7200):
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


def sd_status():
    st = {"online": sd_alive(), "api": SD_API, "sd_home": SD_HOME, "model": None,
          "animatediff": False, "ad_models": [], "console_port": PORT,
          "puppet": MOTION_OK, "actions": list(motion_engine.ACTIONS) if MOTION_OK else []}
    if st["online"]:
        try:
            opts = sd_get("/sdapi/v1/options")
            st["model"] = opts.get("sd_model_checkpoint")
        except Exception:
            pass
        ad_dir = os.path.join(SD_HOME, "extensions", "sd-webui-animatediff", "model")
        if os.path.isdir(ad_dir):
            st["ad_models"] = sorted(f for f in os.listdir(ad_dir) if f.endswith(".safetensors"))
        try:
            scr = sd_get("/sdapi/v1/scripts")
            st["animatediff"] = ("AnimateDiff" in (scr.get("txt2img") or [])) or bool(st["ad_models"])
        except Exception:
            st["animatediff"] = bool(st["ad_models"])
    return st


def start_sd():
    """后台拉起 SD1.5 API（无头）。返回 (ok, 说明)"""
    if sd_alive():
        return True, "已经在跑"
    if not os.path.isfile(SD_PY):
        return False, f"找不到 python：{SD_PY}"
    env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", PYTHONUTF8="1")
    log = os.path.join(WORK, "sd_server.log")
    f = open(log, "ab")
    subprocess.Popen([SD_PY, "webui.py", "--api", "--port", "7860", "--medvram",
                      "--opt-sdp-attention", "--skip-version-check", "--no-half-vae"],
                     cwd=SD_HOME, env=env, stdout=f, stderr=f,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for _ in range(90):
        time.sleep(2)
        if sd_alive():
            return True, "已启动"
    return False, f"启动超时，日志：{log}"


# ---------------------------------------------------------------- 动作
def newest_ad_files(since):
    out = []
    for root, _dirs, files in os.walk(AD_OUTPUT):
        for fn in files:
            p = os.path.join(root, fn)
            try:
                if os.path.getmtime(p) >= since - 5:
                    out.append(p)
            except OSError:
                pass
    out.sort(key=os.path.getmtime)
    return out


def job_worker(job, payload, kind):
    t0 = time.time()
    job["state"] = "running"
    job["started"] = t0
    try:
        if kind == "puppet":
            r = motion_engine.render(**{k: v for k, v in payload.items()})
            job["media"] = r["path"]
            try:
                q = quality_check.coherence(r["path"], sheet=os.path.join(WORK, f"{job['id']}_sheet.png"))
                job["coherence"] = q
            except Exception as e:
                job["coherence"] = {"verdict": "SKIP", "error": str(e)[:200]}
            job["elapsed"] = round(time.time() - t0, 1)
            job["state"] = "done"
            return
        if kind == "motion":
            path = "/sdapi/v1/img2img"
        elif kind == "image":
            path = "/sdapi/v1/txt2img"
        else:
            raise ValueError(kind)
        r = sd_post(path, payload)
        imgs = r.get("images") or []
        job["images"] = imgs[:1] if kind == "image" else []
        files = []
        if kind == "motion":
            files = newest_ad_files(t0)
            job["raw_files"] = files[-6:]
            media = [p for p in files if p.lower().endswith((".mp4", ".webm", ".gif"))]
            if not media and imgs:
                media = ["inline"]
            job["media"] = media[-1] if media else None
            if job["media"] and job["media"] != "inline":
                ext = os.path.splitext(job["media"])[1]
                dst = os.path.join(WORK, f"{job['id']}{ext}")
                with open(job["media"], "rb") as s, open(dst, "wb") as d:
                    d.write(s.read())
                job["media"] = dst
        job["elapsed"] = round(time.time() - t0, 1)
        job["state"] = "done"
    except Exception as e:
        job["state"] = "error"
        job["error"] = f"{type(e).__name__}: {str(e)[:500]}"
        job["elapsed"] = round(time.time() - t0, 1)


def build_motion_payload(a):
    frames = int(a.get("frames", 24))
    denoise = float(a.get("denoise", 0.45))
    steps = int(a.get("steps", 20))
    w = int(a.get("width", 512))
    h = int(a.get("height", 512))
    seed = int(a.get("seed", -1))
    fps = int(a.get("fps", 8))
    base = (a.get("prompt") or "").strip()
    action = (a.get("action") or "").strip()
    travel = int(a.get("travel", 1))
    p0 = a.get("phase0") or "arms down, standing still"
    p1 = a.get("phase1") or "raising arms upward"
    p2 = a.get("phase2") or "arms raised high"
    if travel and action:
        prompt = (f"0: {base}, {p0}\n{max(1, frames // 3)}: {base}, {p1}\n"
                  f"{max(2, 2 * frames // 3)}: {base}, {p2}\n{frames - 2}: {base}, {action}")
    else:
        prompt = f"{base}, {action}"
    neg = a.get("negative") or ("cut, jump cut, scene change, extra limbs, bad anatomy, bad hands, "
                                "lowres, worst quality, low quality, blurry, text, watermark")
    ad = {"model": a.get("ad_model") or "mm_sd15_v3.safetensors", "enable": True,
          "video_length": frames, "fps": fps, "loop_number": 0, "closed_loop": "R-P",
          "batch_size": 16, "stride": 1, "overlap": 4, "format": ["MP4"],
          "interp": "Off", "interp_x": 10, "is_i2i_batch": False}
    payload = {"prompt": prompt, "negative_prompt": neg, "steps": steps,
               "cfg_scale": float(a.get("cfg", 7)), "denoising_strength": denoise,
               "width": w, "height": h, "sampler_name": a.get("sampler", "DPM++ 2M Karras"),
               "seed": seed, "batch_size": 1, "n_iter": 1,
               "save_images": True, "send_images": False,
               "alwayson_scripts": {"AnimateDiff": {"args": [ad]}}}
    img = a.get("image_b64")
    if img:
        payload["init_images"] = [img.split(",")[-1]]
    elif a.get("image_path") and os.path.isfile(a["image_path"]):
        with open(a["image_path"], "rb") as f:
            payload["init_images"] = [base64.b64encode(f.read()).decode()]
    else:
        raise ValueError("缺少初始图（上传一张，或先在上面出一张图）")
    return payload


def build_image_payload(a):
    return {"prompt": a.get("prompt") or "", "negative_prompt": a.get("negative") or "",
            "steps": int(a.get("steps", 28)), "cfg_scale": float(a.get("cfg", 7)),
            "width": int(a.get("width", 768)), "height": int(a.get("height", 768)),
            "sampler_name": a.get("sampler", "DPM++ 2M Karras"),
            "seed": int(a.get("seed", -1)), "batch_size": 1, "n_iter": 1,
            "save_images": True, "send_images": True}


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "NeoForgeConsole/1.0"

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
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        if self.path == "/api/status":
            self._json(200, sd_status())
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
                out = {k: v for k, v in job.items() if k != "_payload"}
            if out.get("state") == "done" and out.get("media") and out["media"] != "inline":
                out["media_url"] = "/files/" + os.path.basename(out["media"])
            if out.get("images"):
                out["image_url"] = f"/api/inline/{job['id']}"
            self._json(200, out)
            return
        m = re.match(r"^/api/inline/([0-9a-f]+)$", self.path)
        if m:
            job = JOBS.get(m.group(1))
            if job and job.get("images"):
                raw = base64.b64decode(job["images"][0].split(",")[-1])
                self._send(200, raw, "image/png")
                return
        if self.path.startswith("/files/"):
            name = os.path.basename(urllib.request.unquote(self.path[7:]))
            p = os.path.join(WORK, name)
            if os.path.isfile(p):
                ctype = mimetypes.guess_type(p)[0] or "application/octet-stream"
                with open(p, "rb") as f:
                    self._send(200, f.read(), ctype)
                return
            self._json(404, {"error": "no file"})
            return
        if self.path.startswith("/outputs/"):
            rel = urllib.request.unquote(self.path[9:])
            p = os.path.abspath(os.path.join(AD_OUTPUT, rel))
            if p.startswith(os.path.abspath(AD_OUTPUT)) and os.path.isfile(p):
                ctype = mimetypes.guess_type(p)[0] or "application/octet-stream"
                with open(p, "rb") as f:
                    self._send(200, f.read(), ctype)
                return
            self._json(404, {"error": "no file"})
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

        if self.path == "/api/puppet":
            if not MOTION_OK:
                self._json(503, {"error": f"动作引擎不可用（缺依赖）：{MOTION_ERR}"})
                return
            data = (a.get("image_b64") or "").split(",")[-1]
            sprite = a.get("sprite_path")
            if data:
                sprite = os.path.join(WORK, "sprite_" + uuid.uuid4().hex[:8] + ".png")
                with open(sprite, "wb") as f:
                    f.write(base64.b64decode(data))
            if not sprite or not os.path.isfile(sprite):
                self._json(400, {"error": "缺少立绘：上传一张，或先用①出图"})
                return
            jid = uuid.uuid4().hex[:12]
            size = a.get("size", "720x720")
            w, h = (int(v) for v in size.lower().split("x"))
            bg = a.get("bg") or None
            if bg == "__corn":
                bg = CORN_BG if os.path.isfile(CORN_BG) else None
            payload = {
                "sprite": sprite,
                "action": a.get("action", "wave"),
                "dur": float(a.get("dur", 3.0)),
                "fps": int(a.get("fps", 24)),
                "size": (w, h),
                "bg": bg,
                "seed": int(a.get("seed", 7)),
                "out": os.path.join(WORK, f"{jid}.mp4"),
            }
            job = {"id": jid, "kind": "puppet", "state": "queued", "created": time.time()}
            with JOBS_LOCK:
                JOBS[jid] = job
            threading.Thread(target=job_worker, args=(job, payload, "puppet"), daemon=True).start()
            self._json(200, {"job_id": jid})
            return

        if self.path in ("/api/image", "/api/motion"):
            if not sd_alive():
                self._json(503, {"error": "SD 后端未启动，先点『启动后端』"})
                return
            kind = "image" if self.path == "/api/image" else "motion"
            try:
                payload = build_image_payload(a) if kind == "image" else build_motion_payload(a)
            except Exception as e:
                self._json(400, {"error": str(e)})
                return
            jid = uuid.uuid4().hex[:12]
            job = {"id": jid, "kind": kind, "state": "queued", "created": time.time()}
            with JOBS_LOCK:
                JOBS[jid] = job
            threading.Thread(target=job_worker, args=(job, payload, kind), daemon=True).start()
            self._json(200, {"job_id": jid})
            return

        if self.path == "/api/save_upload":
            data = (a.get("image_b64") or "").split(",")[-1]
            if not data:
                self._json(400, {"error": "no image"})
                return
            p = os.path.join(WORK, "upload_" + uuid.uuid4().hex[:8] + ".png")
            with open(p, "wb") as f:
                f.write(base64.b64decode(data))
            self._json(200, {"path": p})
            return

        self._json(404, {"error": "not found"})


PAGE = r"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>NeoForgeSD 控制台</title>
<style>
:root{--bg:#0f1116;--card:#171a21;--line:#262b36;--fg:#e8ecf3;--dim:#8b94a7;--acc:#4ade80;--acc2:#38bdf8;--warn:#fbbf24;--bad:#f87171}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.6 "Microsoft YaHei",system-ui,sans-serif}
header{display:flex;align-items:center;gap:14px;padding:14px 20px;border-bottom:1px solid var(--line);position:sticky;top:0;background:#0f1116ee;backdrop-filter:blur(6px);z-index:9}
.logo{font-weight:700;font-size:17px;letter-spacing:.5px}.logo i{color:var(--acc2);font-style:normal}
.mono{font-family:Consolas,monospace;font-size:12px;color:var(--dim)}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:6px;background:#555}
.dot.on{background:var(--acc);box-shadow:0 0 8px var(--acc)}.dot.off{background:var(--bad)}
main{display:grid;grid-template-columns:minmax(360px,1fr) minmax(380px,1.1fr);gap:16px;padding:16px;max-width:1400px;margin:0 auto}
@media(max-width:900px){main{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
.card h2{margin:0 0 12px;font-size:15px;display:flex;justify-content:space-between;align-items:center}
.card h2 span{font-weight:400;font-size:12px;color:var(--dim)}
label{display:block;font-size:12px;color:var(--dim);margin:10px 0 4px}
input,textarea,select{width:100%;background:#0d1015;border:1px solid var(--line);color:var(--fg);border-radius:8px;padding:8px 10px;font:inherit;outline:none}
input:focus,textarea:focus,select:focus{border-color:var(--acc2)}
textarea{resize:vertical;min-height:70px}
.row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}
button{background:var(--acc2);color:#04121b;border:0;border-radius:8px;padding:10px 16px;font-weight:700;cursor:pointer;font:inherit}
button.ghost{background:transparent;color:var(--fg);border:1px solid var(--line)}
button:disabled{opacity:.45;cursor:not-allowed}
.act{display:flex;gap:10px;margin-top:14px;flex-wrap:wrap}
.bar{height:6px;background:#0d1015;border-radius:4px;overflow:hidden;margin-top:10px;border:1px solid var(--line)}
.bar>i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--acc2),var(--acc));transition:width .3s}
.msg{margin-top:10px;font-size:12px;color:var(--dim);white-space:pre-wrap;word-break:break-all}
.msg.err{color:var(--bad)}
.stage{min-height:300px;display:flex;align-items:center;justify-content:center;border:1px dashed var(--line);border-radius:10px;color:var(--dim);overflow:hidden;background:#0d1015}
.stage img,.stage video{max-width:100%;max-height:60vh;display:block}
.note{font-size:12px;color:var(--dim);margin-top:8px;line-height:1.7}
.k{color:var(--fg)}
.chip{display:inline-block;border:1px solid var(--line);border-radius:99px;padding:2px 9px;font-size:11px;margin:2px 4px 0 0;color:var(--dim);cursor:pointer}
.chip:hover{border-color:var(--acc2);color:var(--fg)}
</style></head><body>
<header>
  <div class="logo">NeoForge<i>SD</i> 控制台</div>
  <div class="mono" id="stat"><span class="dot"></span>连接中…</div>
  <div style="flex:1"></div>
  <button class="ghost" onclick="startSD()" id="btnStart">启动后端</button>
  <button class="ghost" onclick="refresh()">刷新状态</button>
</header>
<main>
  <section class="card">
    <h2>① 出图 <span>txt2img · 生成立绘/场景</span></h2>
    <label>提示词</label>
    <textarea id="p1">masterpiece, best quality, 1girl, chibi, penguin costume, penguin hood, mint blue hair, blue eyes, standing in a cornfield, corn stalks, warm afternoon sunlight</textarea>
    <label>反向词</label>
    <input id="n1" value="lowres, worst quality, low quality, blurry, bad anatomy, bad hands, text, watermark">
    <div class="row3">
      <div><label>宽</label><input id="w1" type="number" value="768"></div>
      <div><label>高</label><input id="h1" type="number" value="768"></div>
      <div><label>步数</label><input id="s1" type="number" value="28"></div>
    </div>
    <div class="act"><button onclick="genImage()" id="btnImg">生成图片</button>
      <button class="ghost" onclick="useAsInit()">用它当动作首帧</button></div>
    <div class="bar"><i id="bar1"></i></div>
    <div class="msg" id="m1"></div>
  </section>

  <section class="card">
    <h2>② 动作 <span>i2v + AnimateDiff · 图生视频</span></h2>
    <label>初始图（上传，或用①出的图）</label>
    <input id="fInit" type="file" accept="image/*" onchange="pickInit(this)">
    <div class="note" id="initNote">未选择初始图。<span class="chip" onclick="useAsInit()">用左侧生成的图</span></div>
    <label>场景/人物描述（贯穿全片的稳定描述）</label>
    <textarea id="p2">masterpiece, best quality, 1girl, chibi, penguin costume, penguin hood, mint blue hair, blue eyes, standing in a cornfield, corn stalks, warm afternoon sunlight</textarea>
    <label>动作（最后的姿势/状态）</label>
    <input id="act2" value="both arms raised high, waving, open palms, smiling">
    <div class="row3">
      <div><label>帧数</label><select id="fr"><option>16</option><option selected>24</option><option>32</option></select></div>
      <div><label>扰动 denoise</label><input id="dn" type="number" step="0.05" value="0.45"></div>
      <div><label>步数</label><input id="st2" type="number" value="20"></div>
    </div>
    <div class="act"><button onclick="genMotion()" id="btnVid">生成动作视频</button></div>
    <div class="bar"><i id="bar2"></i></div>
    <div class="msg" id="m2"></div>
    <div class="note">经验值：512×512 / 24 帧 ≈ 3 秒 @8fps，本机 4G 显存约 3–6 分钟。帧数越大动作越长，超过 32 帧显存吃紧。</div>
  </section>

  <section class="card">
    <h2>③ 木偶动作 <span>零显存 · 动作可编排 · 绝对连贯</span></h2>
    <label>立绘（和②共用同一张：上传或用①出的图）</label>
    <div class="note" id="initNote2">未选择立绘。<span class="chip" onclick="useAsInit()">用①生成的图</span></div>
    <div class="row3">
      <div><label>动作</label><select id="act3"></select></div>
      <div><label>时长(秒)</label><input id="dur3" type="number" step="0.5" value="3"></div>
      <div><label>帧率</label><select id="fps3"><option>24</option><option>30</option><option>12</option></select></div>
    </div>
    <div class="row3">
      <div><label>尺寸</label><select id="size3"><option>720x720</option><option>512x512</option><option>720x1280</option></select></div>
      <div><label>背景</label><select id="bg3"><option value="">深灰底</option><option value="__corn">示例：玉米地</option></select></div>
      <div><label>种子</label><input id="seed3" type="number" value="7"></div>
    </div>
    <div class="act"><button onclick="genPuppet()" id="btnPup">生成木偶动画</button></div>
    <div class="bar"><i id="bar3"></i></div>
    <div class="msg" id="m3"></div>
    <div class="note" id="q3"></div>
    <div class="note">零显存（纯 CPU 形变场），3 秒 720p 约 10 秒出片。动作由曲线决定，不会漂移、不会糊尾帧。</div>
  </section>

  <section class="card" style="grid-column:1/-1">
    <h2>④ 结果 <span>直接播放 / 下载</span></h2>
    <div class="stage" id="stage">还没有产物</div>
  </section>
</main>
<script>
let STATUS={}, INIT_B64=null, LAST_IMG=null, TIMER=null;
const $=id=>document.getElementById(id);
function esc(s){return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;')}
function setMsg(id,t,err){const e=$(id);e.textContent=t||'';e.className='msg'+(err?' err':'')}
async function refresh(){
  try{const r=await fetch('/api/status');STATUS=await r.json();}catch(e){STATUS={online:false}}
  const s=$('stat');
  s.innerHTML=STATUS.online?`<span class="dot on"></span>SD 在线 · ${esc(STATUS.model||'模型?')} · AnimateDiff ${STATUS.animatediff?'可用':'缺失'}`
                        :`<span class="dot off"></span>SD 离线（${esc(STATUS.api||'')}）`;
  $('btnStart').style.display=STATUS.online?'none':'inline-block';
  if(STATUS.actions && STATUS.actions.length && $('act3') && !$('act3').options.length){
    const zh={idle:'待机呼吸',wave:'招手',raise:'举双手',nod:'点头',jump:'跳跃',reach:'伸手取物',punch:'出拳',bow:'鞠躬'};
    STATUS.actions.forEach(a=>{const o=document.createElement('option');o.value=a;
      o.textContent=a+(zh[a]?'（'+zh[a]+'）':'');$('act3').appendChild(o);});
    $('act3').value='wave';
  }
}
async function startSD(){
  $('btnStart').disabled=true;$('btnStart').textContent='启动中…(约60~120秒)';
  const r=await fetch('/api/start_sd',{method:'POST'});const j=await r.json();
  $('btnStart').disabled=false;$('btnStart').textContent='启动后端';
  alert(j.message);refresh();
}
function pickInit(inp){
  const f=inp.files[0]; if(!f)return;
  const rd=new FileReader();
  rd.onload=()=>{INIT_B64=rd.result;
    const html='已选择：'+esc(f.name)+' <span class="chip" onclick="clearInit()">清除</span>';
    $('initNote').innerHTML=html; if($('initNote2'))$('initNote2').innerHTML=html;};
  rd.readAsDataURL(f);
}
function clearInit(){INIT_B64=null;$('fInit').value='';
  $('initNote').textContent='未选择初始图。';
  if($('initNote2'))$('initNote2').textContent='未选择立绘。';}
function useAsInit(){
  if(!LAST_IMG){alert('先出一张图，或者上传一张');return}
  INIT_B64=LAST_IMG;
  const html='已使用①生成的图 <span class="chip" onclick="clearInit()">清除</span>';
  $('initNote').innerHTML=html; if($('initNote2'))$('initNote2').innerHTML=html;
  $('stage').innerHTML='<img src="'+LAST_IMG+'">';
}
async function poll(jid,barId,msgId,kind){
  const t0=Date.now();
  clearInterval(TIMER);
  TIMER=setInterval(async()=>{
    let pr=0;
    try{const p=await (await fetch('/api/progress')).json();pr=(p.progress||0)*100;}catch(e){}
    $(barId).style.width=pr.toFixed(0)+'%';
    const j=await (await fetch('/api/job/'+jid)).json();
    const el=((Date.now()-t0)/1000).toFixed(0);
    if(j.state==='done'){
      clearInterval(TIMER);$(barId).style.width='100%';
      setMsg(msgId,`完成 · 用时 ${j.elapsed}s`+(kind==='motion'?`\n文件：${j.media||'（无视频）'}`:''));
      if(kind==='image'){LAST_IMG=j.image_url;$('stage').innerHTML='<img src="'+j.image_url+'">';}
      else if(j.media_url){$('stage').innerHTML='<video src="'+j.media_url+'" controls autoplay loop></video>';}
      else {$('stage').innerHTML='<div style="padding:20px;color:#fbbf24">后端没有返回视频，去看输出目录</div>';}
      loadBtn(true);
    }else if(j.state==='error'){
      clearInterval(TIMER);setMsg(msgId,'失败：'+j.error,true);loadBtn(true);
    }else{setMsg(msgId,`生成中… ${el}s ｜ 进度 ${pr.toFixed(0)}%`);}
  },1500);
}
function loadBtn(on){$('btnImg').disabled=!on;$('btnVid').disabled=!on;}
async function genImage(){
  loadBtn(false);setMsg('m1','提交中…');$('m1').className='msg';$('bar1').style.width='0';
  const r=await fetch('/api/image',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    prompt:$('p1').value,negative:$('n1').value,width:+$('w1').value,height:+$('h1').value,steps:+$('s1').value})});
  const j=await r.json();
  if(j.error){setMsg('m1',j.error,true);loadBtn(true);return}
  poll(j.job_id,'bar1','m1','image');
}
async function genMotion(){
  if(!INIT_B64){alert('先选初始图：上传一张，或先用①生成');return}
  loadBtn(false);setMsg('m2','提交中…');$('m2').className='msg';$('bar2').style.width='0';
  const r=await fetch('/api/motion',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    image_b64:INIT_B64,prompt:$('p2').value,action:$('act2').value,
    frames:+$('fr').value,denoise:+$('dn').value,steps:+$('st2').value})});
  const j=await r.json();
  if(j.error){setMsg('m2',j.error,true);loadBtn(true);return}
  poll(j.job_id,'bar2','m2','motion');
}
async function genPuppet(){
  if(!INIT_B64){alert('先选立绘：上传一张，或先用①生成');return}
  $('btnPup').disabled=true;setMsg('m3','提交中…');$('m3').className='msg';$('q3').textContent='';$('bar3').style.width='0';
  const r=await fetch('/api/puppet',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    image_b64:INIT_B64,action:$('act3').value,dur:+$('dur3').value,fps:+$('fps3').value,
    size:$('size3').value,seed:+$('seed3').value,bg:$('bg3').value})});
  const j=await r.json();
  if(j.error){setMsg('m3',j.error,true);$('btnPup').disabled=false;return}
  pollPuppet(j.job_id);
}
async function pollPuppet(jid){
  const t0=Date.now();
  const tick=async()=>{
    const j=await (await fetch('/api/job/'+jid)).json();
    const el=((Date.now()-t0)/1000).toFixed(0);
    if(j.state==='done'){
      $('bar3').style.width='100%';$('btnPup').disabled=false;
      setMsg('m3','完成 · 用时 '+j.elapsed+'s');
      if(j.media_url){$('stage').innerHTML='<video src="'+j.media_url+'" controls autoplay loop></video>';}
      const q=j.coherence||{};
      if(q.verdict){
        const ok=q.verdict==='COHERENT';
        $('q3').innerHTML='连贯性自检：<b style="color:'+(ok?'#4ade80':'#fbbf24')+'">'+q.verdict+'</b>'+
          ' · 帧数 '+q.frames+' · 运动量 '+(q.diff_mean??'-')+' · 抖动 '+(q.diff_cv??'-')+
          ' · 跳变 '+((q.duplicate_frames||[]).length)+' · 漂移 '+(q.color_drift_end??'-')+
          (ok?'':' · 未过项：'+(q.failed_on||[]).join(','));
      }
    }else if(j.state==='error'){
      $('btnPup').disabled=false;setMsg('m3','失败：'+j.error,true);
    }else{setMsg('m3','渲染中… '+el+'s');$('bar3').style.width=Math.min(90,el*7)+'%';setTimeout(tick,700);}
  };
  tick();
}
refresh();setInterval(refresh,15000);
</script></body></html>
"""


def main():
    print(f"NeoForgeSD 控制台  → http://127.0.0.1:{PORT}")
    print(f"后端 SD API: {SD_API}   SD 目录: {SD_HOME}")
    print(f"产物目录: {WORK}")
    st = sd_status()
    print("SD 状态:", "在线" if st["online"] else "离线（页面里可点『启动后端』）", "·", st.get("model"))
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
