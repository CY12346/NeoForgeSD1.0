# -*- coding: utf-8 -*-
"""HTTP 层：唯一对外说话的地方。只做三件事——解参数、调下层、编码响应。

依赖方向（不许反）：server → engine / jobs / payload / history / textfmt / sdapi → config
"""
import json
import mimetypes
import os
import re
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, engine, history, jobs, payload, sdapi, textfmt

HOST = "127.0.0.1"
MAX_BODY = 64 * 1024 * 1024        # 64 MB：放得下 4 MB 的 PNG base64 + 余量


# ---------------------------------------------------------------- 状态
def status():
    """给 /api/status：SD 在线状态 + 目录 + 引擎状态 + 目录清单。健康检查就打它。"""
    online = sdapi.alive()
    st = {"online": online, "api": config.SD_API, "sd_home": config.SD_HOME,
          "work": config.WORK, "port": config.PORT,
          "model": None, "models": [], "samplers": [], "schedulers": [],
          "upscalers": [], "loras": [], "opts": {}, "backend": engine.status()}
    if online:
        cat = sdapi.catalog()
        st.update(cat)
        st["model"] = (cat.get("opts") or {}).get("model") or st.get("model")
        if engine.START["state"] != "starting":
            engine.START.update(state="ready", msg="已在线")
    return st


def load_page():
    """页面是独立 HTML 文件：改界面不用动 Python，也不怕转义炸。"""
    try:
        with open(config.PAGE_FILE, encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return ("<!doctype html><meta charset='utf-8'><h1>页面读取失败</h1>"
                "<pre>%s\n应当存在：%s</pre>" % (e, config.PAGE_FILE))


# ---------------------------------------------------------------- 路由
class Handler(BaseHTTPRequestHandler):
    server_version = "NeoForgeImage/1.3"

    def log_message(self, fmt, *args):      # 静音：日志由调用方决定去哪
        pass

    # ---- 输出
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

    def _need_dict(self):
        """词典不可用时统一的 503 人话。"""
        return {"error": "提示词词典不可用：" + textfmt.error() if textfmt.error()
                else "提示词词典不可用"}

    # ---- GET
    def do_GET(self):
        path = self.path.split("?")[0]

        if path in ("/", "/index.html"):
            self._send(200, load_page().encode("utf-8"), "text/html; charset=utf-8")
            return
        if self.path.startswith("/api/status"):
            self._json(200, status())
            return
        if self.path.startswith("/api/catalog"):
            self._json(200, sdapi.catalog(force="force" in self.path))
            return
        if self.path.startswith("/api/i18n"):
            if not textfmt.available():
                self._json(503, self._need_dict())
                return
            try:
                self._json(200, textfmt.stats())
            except Exception as e:
                self._json(500, {"error": "%s: %s" % (type(e).__name__, str(e)[:200])})
            return
        if self.path.startswith("/api/suggest"):
            if not textfmt.available():
                self._json(503, self._need_dict())
                return
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            q = (qs.get("q") or [""])[0]
            try:
                limit = int((qs.get("limit") or ["15"])[0])
            except ValueError:
                limit = 15
            self._json(200, textfmt.complete(q, limit))     # 词典首次加载约 1.5s
            return
        if self.path.startswith("/api/promptlib"):
            if not textfmt.available():
                self._json(503, self._need_dict())
                return
            self._json(200, textfmt.promptlib())
            return
        if self.path.startswith("/api/styles"):
            self._json(200, sdapi.styles())
            return
        if self.path.startswith("/api/history"):
            try:
                limit = int(self.path.split("limit=")[1].split("&")[0]) if "limit=" in self.path else 24
            except Exception:
                limit = 24
            self._json(200, {"items": history.recent(limit)})
            return
        if self.path.startswith("/api/progress"):
            try:
                self._json(200, sdapi.get("/sdapi/v1/progress"))
            except Exception as e:
                self._json(200, {"progress": 0, "online": False, "error": str(e)[:200]})
            return
        m = re.match(r"^/api/job/([0-9a-f]+)$", path)
        if m:
            job = jobs.get(m.group(1))
            if not job:
                self._json(404, {"error": "no such job"})
                return
            self._json(200, job)
            return
        if path.startswith("/files/"):
            name = os.path.basename(urllib.request.unquote(path[7:]))
            self._file(os.path.join(config.WORK, name))
            return
        self._json(404, {"error": "not found"})

    # ---- POST
    def do_POST(self):
        ln = int(self.headers.get("Content-Length") or 0)
        if ln > MAX_BODY:
            self._json(413, {"error": "请求太大（上限 %d MB）" % (MAX_BODY // 1024 // 1024)})
            return
        try:
            a = json.loads(self.rfile.read(ln).decode("utf-8")) if ln else {}
        except Exception:
            self._json(400, {"error": "bad json"})
            return
        path = self.path.split("?")[0]

        if path == "/api/start_sd":
            ok, msg = engine.start()
            self._json(200, {"ok": ok, "message": msg, "status": status()})
            return

        if path == "/api/stop_sd":
            ok, msg = engine.stop()
            self._json(200, {"ok": ok, "message": msg})
            return

        if path == "/api/options":
            if not sdapi.alive():
                self._json(503, {"error": "SD 后端未启动"})
                return
            body = {}
            if a.get("clip_skip") is not None:
                body["CLIP_stop_at_last_layers"] = max(1, min(12, int(a["clip_skip"])))
            if not body:
                self._json(400, {"error": "没有可设置的选项"})
                return
            try:
                sdapi.post("/sdapi/v1/options", body, timeout=60)
                sdapi.invalidate()
                self._json(200, {"ok": True, "opts": sdapi.catalog(force=True).get("opts")})
            except Exception as e:
                self._json(500, {"error": f"{type(e).__name__}: {str(e)[:300]}"})
            return

        if path == "/api/vae":
            if not sdapi.alive():
                self._json(503, {"error": "SD 后端未启动"})
                return
            name = (a.get("name") or "").strip()
            try:
                sdapi.post("/sdapi/v1/options", {"sd_vae": name}, timeout=300)
                sdapi.invalidate()
                self._json(200, {"ok": True, "opts": sdapi.catalog(force=True).get("opts")})
            except Exception as e:
                self._json(500, {"error": f"{type(e).__name__}: {str(e)[:300]}"})
            return

        if path == "/api/pnginfo":
            if not sdapi.alive():
                self._json(503, {"error": "SD 后端未启动"})
                return
            img = a.get("image_b64") or ""
            if not img:
                self._json(400, {"error": "缺少 image_b64"})
                return
            try:
                r = sdapi.post("/sdapi/v1/png-info", {"image": img}, timeout=120)
                self._json(200, {"ok": True, "info": r.get("info") or ""})
            except Exception as e:
                self._json(500, {"error": f"{type(e).__name__}: {str(e)[:300]}"})
            return

        if path == "/api/translate":
            if not textfmt.available():
                self._json(503, self._need_dict())
                return
            try:
                self._json(200, textfmt.translate(a.get("text") or "",
                                                  a.get("dir") or "zh2en",
                                                  bool(a.get("web", True))))
            except Exception as e:
                self._json(500, {"error": "%s: %s" % (type(e).__name__, str(e)[:300])})
            return

        if path == "/api/open_folder":
            try:
                os.startfile(config.WORK)  # noqa: S606  (Windows 资源管理器)
                self._json(200, {"ok": True, "folder": config.WORK})
            except Exception as e:
                self._json(200, {"ok": False, "error": str(e)[:200]})
            return

        if path == "/api/model":
            if not sdapi.alive():
                self._json(503, {"error": "SD 后端未启动"})
                return
            name = (a.get("name") or "").strip()
            if not name:
                self._json(400, {"error": "缺少 name"})
                return
            try:
                opts = {"sd_model_checkpoint": name}
                v = config.vae_for(name)
                if v:
                    opts["sd_vae"] = v          # SDXL 系自动换配套 VAE，否则花屏
                sdapi.post("/sdapi/v1/options", opts, timeout=600)
                sdapi.invalidate()
                o = sdapi.get("/sdapi/v1/options", 20)
                self._json(200, {"ok": True, "model": o.get("sd_model_checkpoint"),
                                 "vae": o.get("sd_vae")})
            except Exception as e:
                self._json(500, {"error": f"{type(e).__name__}: {str(e)[:300]}"})
            return

        if path in ("/api/image", "/api/img2img", "/api/upscale"):
            if not sdapi.alive():
                self._json(503, {"error": "SD 后端未启动，先点『启动后端』"})
                return
            kind = {"image": "image", "img2img": "edit", "upscale": "upscale"}[path.rsplit("/", 1)[-1]]
            try:
                body = payload.BUILDERS[kind](a)
            except Exception as e:
                self._json(400, {"error": str(e)})
                return
            self._json(200, {"job_id": jobs.submit(kind, body)})
            return

        self._json(404, {"error": "not found"})


# ---------------------------------------------------------------- 起服务
def make_server(port=None, host=None):
    return ThreadingHTTPServer((host or HOST, port or config.PORT), Handler)


def serve(on_ready=None):
    """起服务并阻塞。on_ready(port) 在**端口真正绑上之后**调用（内核拿它发 PIPE:READY）。"""
    srv = make_server()
    if on_ready:
        try:
            on_ready(srv.server_address[1])
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
