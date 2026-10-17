#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
manhua_engine · ComfyUI 后端客户端（可复用层之二）
==================================================
设计目标：产线上层脚本只认 `txt2img()` 这一个接口，底层是 Forge 还是 ComfyUI 由配置决定。
→ 换引擎不动业务代码，这就是"可复用"的具体落地方式。

ComfyUI HTTP API（实测稳定，2026 的 0.41.0）：
  POST /prompt            {"prompt": <workflow图>, "client_id": <uuid>} → {"prompt_id": ...}
  GET  /history/{id}      完成前是 {}，完成后含 outputs.images[].{filename,subfolder,type}
  GET  /view?filename=..&subfolder=..&type=output   → 图字节
  GET  /queue             {"queue_running":[...],"queue_pending":[...]}
  POST /interrupt         中断当前
  GET  /system_stats      显存/设备信息
  注意：ComfyUI 默认端口 8188。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


class Comfy:
    def __init__(self, url: str = "http://127.0.0.1:8188", retries: int = 3):
        self.url = url.rstrip("/")
        self.client_id = str(uuid.uuid4())
        self.retries = retries

    # ---------- 低层 ----------
    def _call(self, path, payload=None, timeout=120, method=None):
        data = json.dumps(payload).encode() if payload is not None else None
        last = None
        for i in range(self.retries):
            req = urllib.request.Request(
                self.url + path, data=data,
                headers={"Content-Type": "application/json"},
                method=method or ("POST" if data is not None else "GET"))
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    raw = r.read()
                    return json.loads(raw.decode()) if raw[:1] in (b"{", b"[") else raw
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code}: {e.read()[:300].decode('utf-8','replace')}"
                if e.code in (404, 502, 503) and i < self.retries - 1:
                    time.sleep(2)
                    continue
                raise RuntimeError(f"{path} -> {last}") from e
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
                if i < self.retries - 1:
                    time.sleep(2)
                    continue
                raise RuntimeError(f"{path} 连不上：{last}") from e
        raise RuntimeError(last)

    def get(self, path, timeout=120):
        return self._call(path, None, timeout, method="GET")

    def post(self, path, payload, timeout=300):
        return self._call(path, payload, timeout, method="POST")

    # ---------- 探活 / 状态 ----------
    def alive(self) -> bool:
        try:
            self.get("/system_stats", timeout=8)
            return True
        except Exception:
            return False

    def stats(self):
        return self.get("/system_stats")

    def queue_state(self):
        q = self.get("/queue")
        return {"running": len(q.get("queue_running", [])),
                "pending": len(q.get("queue_pending", []))}

    def interrupt(self):
        return self.post("/interrupt", {})

    # ---------- 出图 ----------
    def queue_prompt(self, workflow: dict) -> str:
        res = self.post("/prompt", {"prompt": workflow, "client_id": self.client_id})
        if "prompt_id" not in res:
            raise RuntimeError(f"提交失败：{json.dumps(res, ensure_ascii=False)[:400]}")
        return res["prompt_id"]

    def wait(self, prompt_id: str, timeout: int = 1800, poll: float = 1.5):
        """等出图完成；返回 history 条目。带进度打印。"""
        t0 = time.time()
        last_note = 0
        while time.time() - t0 < timeout:
            h = self.get(f"/history/{prompt_id}")
            if prompt_id in h:
                entry = h[prompt_id]
                status = (entry.get("status") or {})
                if status.get("status_str") == "error" or status.get("completed") is False:
                    msgs = status.get("messages") or []
                    raise RuntimeError("ComfyUI 执行报错: " + json.dumps(msgs, ensure_ascii=False)[:600])
                return entry
            if time.time() - last_note > 15:
                try:
                    q = self.queue_state()
                    print(f"     ...等待中 {time.time()-t0:.0f}s  队列 运行{q['running']}/待{q['pending']}",
                          flush=True)
                except Exception:
                    pass
                last_note = time.time()
            time.sleep(poll)
        raise TimeoutError(f"等待 {timeout}s 仍未完成（prompt_id={prompt_id}）")

    def images(self, entry) -> list[bytes]:
        out = []
        for node in (entry.get("outputs") or {}).values():
            for im in (node.get("images") or []):
                q = urllib.parse.urlencode({
                    "filename": im.get("filename", ""),
                    "subfolder": im.get("subfolder", ""),
                    "type": im.get("type", "output"),
                })
                out.append(self.get(f"/view?{q}", timeout=180))
        return out

    def txt2img(self, workflow: dict, timeout: int = 1800) -> list[bytes]:
        """一条龙：提交 → 等待 → 取图，返回 PNG bytes 列表"""
        pid = self.queue_prompt(workflow)
        t0 = time.time()
        entry = self.wait(pid, timeout=timeout)
        imgs = self.images(entry)
        print(f"     ✓ {len(imgs)} 张图，用时 {time.time()-t0:.1f}s", flush=True)
        return imgs

    # ---------- 工作流小工具 ----------
    @staticmethod
    def set_input(workflow: dict, node_id: str, key: str, value):
        workflow[node_id]["inputs"][key] = value
        return workflow

    @staticmethod
    def save(png: bytes, path: str) -> str:
        import os
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "wb") as f:
            f.write(png)
        return os.path.abspath(path)


if __name__ == "__main__":
    c = Comfy()
    print("alive:", c.alive())
    if c.alive():
        s = c.stats()
        dev = (s.get("devices") or [{}])[0]
        print("设备:", dev.get("name"), "| 显存 %.1fGB" % ((dev.get("vram_total") or 0) / 2**30))
        print("队列:", c.queue_state())
