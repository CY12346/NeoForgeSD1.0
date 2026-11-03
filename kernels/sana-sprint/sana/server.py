# -*- coding: utf-8 -*-
"""唯一的 HTTP 入口。形状照 neoforge-sd15 那套来 —— 壳只认这个，不认识后面是谁。

    GET  /                 一句人话（壳不读，人偶尔会点开）
    GET  /api/status       内核/模型/显存状态
    GET  /api/progress     当前任务进度（SANA 是一次跑完，只报 running/done）
    GET  /api/job/<id>     单个任务的状态与产出
    GET  /api/history      最近出图
    GET  /files/<name>     取回产出文件
    POST /api/image        {prompt, negative, width, height, steps(2), cfg, seed} → {job_id}
    POST /api/start_sd     兼容壳的老按钮：SANA 不需要后端子进程，直接回 ok
"""
import json
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, pipeline

SYSTEM_PROMPT = ("You are an expert prompt engineer. Rewrite the user's prompt into one vivid "
                 "English sentence describing subject, setting, light, materials and camera, "
                 "then return only the sentence.")

JOBS = {}
LOCK = threading.Lock()
KEEP = 40
HISTORY = []


def _remember(job):
    with LOCK:
        JOBS[job["id"]] = job
        if len(JOBS) > KEEP:
            for old in sorted(JOBS.values(), key=lambda j: j.get("created") or 0)[:-KEEP]:
                JOBS.pop(old["id"], None)


def _run(job, params):
    t0 = time.time()
    job["state"] = "running"
    try:
        path, secs, peak, seed = pipeline.generate(params, job["id"])
        job["files"] = [path]
        job["image_urls"] = ["/files/" + os.path.basename(path)]
        job["seed"] = seed
        job["peak_vram_gb"] = peak
        job["elapsed"] = secs
        job["state"] = "done"
        with LOCK:
            HISTORY.insert(0, {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": "image",
                               "files": [os.path.basename(path)], "prompt": params["prompt"],
                               "width": params["width"], "height": params["height"],
                               "steps": params["steps"], "seed": seed, "elapsed": secs})
            del HISTORY[40:]
    except Exception as e:                      # noqa: BLE001
        job["state"] = "error"
        job["error"] = "%s: %s" % (type(e).__name__, str(e)[:400])
        job["elapsed"] = round(time.time() - t0, 2)


class Handler(BaseHTTPRequestHandler):
    server_version = "NeoForgeSD-sana/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):          # 静音：stdout 是管道，只许 PIPE: 行
        pass

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        if path in ("/", "/index.html"):
            body = ("<h2>NeoForgeSD · sana-sprint 内核</h2>"
                    "<p>SANA-Sprint 0.6B（NVIDIA Linear DiT）。界面在壳里，这里只有 API。</p>"
                    "<p>POST /api/image 出图 · GET /api/status 看状态</p>").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path.startswith("/api/status"):
            st = config.status()
            st.update({"online": True, "dialect": "sana", "backend": {"state": "ready"}}
                      )
            self._json(200, st)
            return

        if path.startswith("/api/progress"):
            running = [j for j in JOBS.values() if j.get("state") == "running"]
            self._json(200, {"progress": 0.5 if running else 0.0, "online": True,
                             "state": "running" if running else "idle"})
            return

        if path.startswith("/api/history"):
            with LOCK:
                self._json(200, {"items": list(HISTORY)})
            return

        if path.startswith("/api/job/"):
            jid = path.rsplit("/", 1)[-1]
            with LOCK:
                job = JOBS.get(jid)
            if not job:
                self._json(404, {"error": "no such job"})
            else:
                self._json(200, dict(job))
            return

        if path.startswith("/files/"):
            name = os.path.basename(urllib.parse.unquote(path[7:]))
            full = os.path.join(config.WORK, name)
            if not os.path.isfile(full):
                self._json(404, {"error": "not found"})
                return
            with open(full, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        self._json(404, {"error": "not found"})

    def do_POST(self):
        ln = int(self.headers.get("Content-Length") or 0)
        if ln > 16 << 20:
            self._json(413, {"error": "请求太大（上限 16 MB）"})
            return
        try:
            a = json.loads(self.rfile.read(ln).decode("utf-8")) if ln else {}
        except Exception:                        # noqa: BLE001
            self._json(400, {"error": "bad json"})
            return
        path = urllib.parse.urlparse(self.path).path

        if path == "/api/start_sd":
            # 壳的老按钮：sd15 那里是起 A1111 子进程。SANA 常驻内存，没什么可起的。
            self._json(200, {"ok": True, "message": "SANA 常驻，无需启动后端",
                             "status": config.status()})
            return

        if path == "/api/stop_sd":
            self._json(200, {"ok": True, "message": "SANA 没有可停的后端进程"})
            return

        if path in ("/api/image", "/api/img2img", "/api/upscale"):
            if path != "/api/image":
                self._json(400, {"error": "sana-sprint 内核目前只做 txt2img；"
                                          "img2img/放大走 sd15 内核"})
                return
            if config.PIPE is None:
                self._json(503, {"error": "模型还没加载好，稍等几秒"})
                return
            try:
                params = pipeline.make(a)
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            if not params["prompt"]:
                self._json(400, {"error": "prompt 不能为空"})
                return
            jid = os.urandom(6).hex()
            job = {"id": jid, "kind": "image", "state": "queued", "created": time.time(),
                   "params": {k: params[k] for k in ("prompt", "width", "height", "steps")}}
            _remember(job)
            threading.Thread(target=_run, args=(job, params), daemon=True).start()
            self._json(200, {"job_id": jid})
            return

        self._json(404, {"error": "not found"})


def serve(on_ready=None):
    srv = ThreadingHTTPServer(("127.0.0.1", config.PORT), Handler)
    if on_ready:
        try:
            on_ready(srv.server_address[1])
        except Exception:                        # noqa: BLE001
            pass
    try:
        srv.serve_forever()
    finally:
        srv.server_close()
