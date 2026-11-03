# -*- coding: utf-8 -*-
"""配置层：所有路径、端口、开关都在这里算一次，别处只读。

规矩：这里不 import 本包任何其它模块（它是最底层），也不做网络/进程操作。
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))        # .../neoforge-sd15/neoforge
KERNEL = os.path.dirname(HERE)                           # .../neoforge-sd15
UI_DIR = os.path.join(HERE, "ui")
PAGE_FILE = os.path.join(UI_DIR, "image_page.html")


def find_base(start=None):
    """往上找「安装根」：含 backend/python/python.exe 或 models/ 的那层。

    壳会直接把 NEOFORGE_SD_HOME 塞进来；没有壳时（命令行直接跑）就靠这个往上摸。
    """
    cur = os.path.abspath(start or KERNEL)
    for _ in range(5):
        if (os.path.isfile(os.path.join(cur, "backend", "python", "python.exe"))
                or os.path.isdir(os.path.join(cur, "models"))):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return ""


BASE = find_base()
SD_HOME = os.path.abspath(
    os.environ.get("NEOFORGE_SD_HOME") or os.path.join(BASE or os.path.dirname(KERNEL), "backend"))
SD_PY = os.path.join(SD_HOME, "python", "python.exe")
SD_API = (os.environ.get("NEOFORGE_SD_API") or "http://127.0.0.1:7860").rstrip("/")
PORT = int(os.environ.get("NEOFORGE_IMAGE_PORT") or os.environ.get("NEOFORGE_CONSOLE_PORT") or 8899)

WORK = os.path.abspath(
    os.environ.get("NEOFORGE_IMAGE_WORK") or os.environ.get("NEOFORGE_WORK")
    or os.path.join(BASE or os.path.dirname(KERNEL), "work"))
os.makedirs(WORK, exist_ok=True)
HISTORY_FILE = os.path.join(WORK, "history_image.json")
SD_LOG = os.path.join(WORK, "sd_server.log")

# ---- SDXL 必须配 SDXL VAE（沿用 SD1.5 的 vae-ft-mse 会整张蓝紫花屏，2026-10-30 实测）
SDXL_VAE = os.environ.get("NEOFORGE_SDXL_VAE", "madebyollin-sdxl-vae-fp16-fix.safetensors")
SDXL_HINTS = ("sdxl", "sd_xl", "illustrious", "pony", "noob", "xl-v", "-xl")

# ---- 每次生成都带的全局设置：s_min_uncond 只跳负面提示词分支，纯提速、不改画面。
# 刻意不默认开 token_merging：它让 1024² 再快 20%，但合并注意力 token 丢细节。
_OVERRIDE = {"s_min_uncond": 1.0}
if os.environ.get("NEOFORGE_TOME"):
    try:
        _OVERRIDE["token_merging_ratio"] = float(os.environ["NEOFORGE_TOME"])
    except ValueError:
        pass


def vae_for(model_name):
    """按模型名推断该配哪个 VAE；返回 None 表示保持当前设置。"""
    n = (model_name or "").lower()
    return SDXL_VAE if any(h in n for h in SDXL_HINTS) else None


def status_dict():
    """给 /api/status 与日志用的路径快照（不含 SD 在线状态）。"""
    return {"port": PORT, "sd_api": SD_API, "sd_home": SD_HOME, "work": WORK,
            "base": BASE, "kernel": KERNEL, "page": PAGE_FILE}
