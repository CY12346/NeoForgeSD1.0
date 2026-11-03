# -*- coding: utf-8 -*-
"""SD API 客户端：只负责「跟 7860 说话」，不懂任务、不懂 HTTP 前端。

所有对 SD 的调用都从这里出去，换方言（A1111 → ComfyUI）时只改这一层 + engine。
"""
import json
import time
import urllib.request

from . import config
from ._vendor import winproc  # noqa: F401  （保留，供 stop() 的 netstat 解析参照）


def get(path, timeout=8):
    with urllib.request.urlopen(config.SD_API + path, timeout=timeout) as r:
        return json.loads(r.read().decode())


def post(path, payload, timeout=3600):
    req = urllib.request.Request(config.SD_API + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def alive():
    try:
        get("/sdapi/v1/progress", 4)
        return True
    except Exception:
        return False


_MODEL_CACHE = {"t": 0, "data": None}
_STYLE_CACHE = {"t": 0, "data": None}      # SD 的 prompt-styles（中文风格预设）


def invalidate():
    """后端状态变了（切模型/切 VAE/重启）：把缓存打掉，下次重新问。"""
    _MODEL_CACHE.update(t=0, data=None)


def catalog(force=False):
    """模型 / LoRA / 采样器 / 调度器 / 放大模型 / 全局选项 —— 缓存 60s。"""
    if not force and _MODEL_CACHE["data"] and time.time() - _MODEL_CACHE["t"] < 60:
        return _MODEL_CACHE["data"]
    data = {"models": [], "samplers": [], "schedulers": [], "upscalers": [], "loras": [],
            "vaes": [], "opts": {}}
    try:
        data["models"] = [m["title"] for m in get("/sdapi/v1/sd-models", 20)]
        data["samplers"] = [s["name"] for s in get("/sdapi/v1/samplers", 20)]
        data["schedulers"] = [s["name"] for s in get("/sdapi/v1/schedulers", 20)]
        data["upscalers"] = [u["name"] for u in get("/sdapi/v1/upscalers", 20)
                             if u["name"] not in ("None", "Lanczos", "Nearest")]
        data["loras"] = [l["name"] for l in get("/sdapi/v1/loras", 20)]
        data["vaes"] = [v["model_name"] for v in get("/sdapi/v1/sd-vae", 20)]
        o = get("/sdapi/v1/options", 20)
        data["opts"] = {"clip_skip": o.get("CLIP_stop_at_last_layers", 1),
                        "model": o.get("sd_model_checkpoint"),
                        "vae": o.get("sd_vae")}
    except Exception:
        pass
    _MODEL_CACHE.update(t=time.time(), data=data)
    return data


def styles():
    """风格预设（中文）。后端离线时返回空表 + online=False，页面自己降级。"""
    if not alive():
        return {"items": [], "online": False}
    try:
        if not _STYLE_CACHE["data"] or time.time() - _STYLE_CACHE["t"] > 300:
            _STYLE_CACHE["data"] = get("/sdapi/v1/prompt-styles", 20)
            _STYLE_CACHE["t"] = time.time()
        return {"items": _STYLE_CACHE["data"], "online": True}
    except Exception as e:
        return {"items": [], "online": True, "error": str(e)[:200]}
