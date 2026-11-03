# -*- coding: utf-8 -*-
"""sana 包的配置层：路径、端口、以及「模型怎么塞进 4 GB 卡」。

2026-11-04 在本机（RTX 3050 Laptop 4 GB）实测出来的唯一可行摆法：
  text_encoder  Gemma-2B 4.76 GB → **CPU，fp32**（CPU 上 fp16 是软件模拟，5 分钟都出不来）
  transformer   0.6B 1.13 GB     → GPU
  vae           DC-AE 1.19 GB    → GPU
  三段合计上卡 3.42 GiB，512² 峰值 4.13 GiB（1024² 会冲到 6.19 GiB，走内存回退，慢）
"""
import json
import os

KERNEL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.environ.get("SANA_HOME") or r"D:\AIGC\sana-probe"
MODEL = os.path.join(HOME, "model")
WORK = os.environ.get("NEOFORGE_IMAGE_WORK") or os.path.join(
    os.path.dirname(os.path.dirname(KERNEL)), "work")
PORT = int(os.environ.get("NEOFORGE_IMAGE_PORT") or 8898)

PIPE = None          # 懒加载，warmup() 里填
_LOADED_AT = 0.0


def _vae_cfg():
    try:
        with open(os.path.join(MODEL, "vae", "config.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


LATENT_CHANNELS = int(_vae_cfg().get("latent_channels") or 32)
DOWNSAMPLE = 32      # DC-AE 是 32 倍压缩（SD1.5 的 VAE 才 8 倍）


def warmup():
    """把 pipeline 加载好并摆到位。返回 (加载秒数, 上卡占用 GiB)。"""
    import time
    global PIPE, _LOADED_AT
    if PIPE is not None:
        return 0.0, 0.0
    import torch
    _shim_xpu()
    from diffusers import SanaSprintPipeline

    torch.set_num_threads(8)
    t0 = time.time()
    pipe = SanaSprintPipeline.from_pretrained(MODEL)
    pipe.text_encoder = pipe.text_encoder.to(torch.float32)      # CPU 上必须 fp32
    pipe.text_encoder.to("cpu")
    if torch.cuda.is_available():
        pipe.transformer.to("cuda")
        pipe.vae.to("cuda")
        # pipeline 的 _execution_device 会跟着 text_encoder（在 CPU）走，把 latents 拽回 CPU，
        # 跟已经上卡的 transformer 打架。钉在 cuda；文本编码我们在外面单独做。
        cls = type(pipe)
        if not getattr(cls, "_pinned_cuda", False):
            cls._execution_device = property(lambda self: torch.device("cuda"))
            cls._pinned_cuda = True
        vram = torch.cuda.memory_allocated() / 2**30
    else:
        vram = 0.0
    PIPE = pipe
    _LOADED_AT = time.time()
    return _LOADED_AT - t0, vram


def _shim_xpu():
    """diffusers 0.34 在 import 时读 torch.xpu（torch 2.6+ 才有）。只用来注册清缓存入口。"""
    import torch
    if hasattr(torch, "xpu"):
        return
    class _FakeXPU:
        empty_cache = staticmethod(lambda: None)
        is_available = staticmethod(lambda: False)
        device_count = staticmethod(lambda: 0)
        memory_allocated = staticmethod(lambda: 0)
        max_memory_allocated = staticmethod(lambda: 0)
        memory_reserved = staticmethod(lambda: 0)
        max_memory_reserved = staticmethod(lambda: 0)
        synchronize = staticmethod(lambda: None)
    torch.xpu = _FakeXPU()


def status():
    import torch
    return {
        "home": HOME,
        "model": MODEL,
        "work": WORK,
        "port": PORT,
        "loaded": PIPE is not None,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "vram_total_gb": (round(torch.cuda.get_device_properties(0).total_memory / 2**30, 1)
                          if torch.cuda.is_available() else 0),
        "vram_used_gb": (round(torch.cuda.memory_allocated() / 2**30, 2)
                         if torch.cuda.is_available() else 0),
    }
