#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""NeoForgeSD 内核入口 · sana-sprint

第二个内核：跑 SANA-Sprint 0.6B（NVIDIA Linear DiT）。跟 neoforge-sd15 并存，
同一个壳、同一套管道协议、同样的 /api/* 形状 —— 壳不用知道后面是谁。

为什么单独一个内核而不是替换 sd15（2026-11-04 实测）：
  512² 11 s（SD1.5 是 5.7 s）、1024² 51 s 且峰值 6.19 GiB 爆 4 GB 卡；
  更关键的是 SD1.5 的 LoRA / ControlNet / VAE 在 SANA 上一个都用不了。
  所以它只干「高质量单张」这一件事，主力仍是 sd15。

退出码：0 正常停止 ｜ 2 端口占用/启动失败 ｜ 3 环境不全
"""
import json
import os
import sys
import time

KERNEL = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(KERNEL, "kernel.json")
PKG = os.path.join(KERNEL, "sana")

DEFAULT_HOME = r"D:\AIGC\sana-probe"


def emit(line):
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def step(frac, msg):
    emit("PIPE:PROGRESS %.2f %s" % (frac, msg))


def log(msg):
    emit("PIPE:LOG " + msg)


def die(code, msg):
    emit("PIPE:LOG ✗ " + msg)
    sys.stderr.write(msg + "\n")
    sys.exit(code)


def manifest():
    try:
        with open(MANIFEST, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        die(3, "读不到 %s：%s" % (MANIFEST, e))


def check_env(m):
    """SANA_HOME 里要有 model/ 和 venv/。缺哪样说哪样，不静默兜底。"""
    home = os.environ.get("SANA_HOME") or DEFAULT_HOME
    if not os.path.isdir(home):
        die(3, "找不到 SANA 环境目录（SANA_HOME=%r）。这个内核不自带 7.17 GB 权重和独立 venv，"
               "要先用 tools/fetch_repo.py 拉 Efficient-Large-Model/Sana_Sprint_0.6B_1024px_diffusers，"
               "见 KERNEL-sana-sprint.md" % home)
    model = os.path.join(home, "model")
    if not os.path.isfile(os.path.join(model, "model_index.json")):
        die(3, "SANA 模型目录不对：%s 里没有 model_index.json（要 diffusers 目录格式）" % model)
    if not os.path.isdir(os.path.join(home, "venv")):
        log("⚠ 没看到 %s\\venv —— 如果当前 python 装不了 diffusers>=0.33 就得建一个" % home)

    work = os.environ.get("NEOFORGE_IMAGE_WORK") or os.path.join(
        os.path.dirname(os.path.dirname(KERNEL)), "work")
    os.makedirs(work, exist_ok=True)
    port = int(os.environ.get("NEOFORGE_IMAGE_PORT") or m.get("port") or 8898)
    os.environ.setdefault("NEOFORGE_IMAGE_PORT", str(port))
    os.environ["SANA_HOME"] = home
    os.environ["NEOFORGE_IMAGE_WORK"] = work
    return home, work, port


def main():
    t0 = time.time()
    m = manifest()
    log("sana-sprint 内核 v%s 启动（%s）" % (m.get("version", "?"), KERNEL))

    step(0.10, "检查环境")
    home, work, port = check_env(m)
    log("SANA_HOME=%s · WORK=%s · 端口=%d" % (home, work, port))
    step(0.30, "环境就绪")

    missing = [p for p in ("config.py", "pipeline.py", "server.py")
               if not os.path.isfile(os.path.join(PKG, p))]
    if missing:
        die(3, "内核代码不完整，缺：%s" % "、".join(missing))

    if KERNEL not in sys.path:
        sys.path.insert(0, KERNEL)
    step(0.45, "载入内核代码")
    try:
        from sana import server                     # noqa: E402
        from sana import config as kconfig          # noqa: E402
    except Exception as e:
        die(2, "内核代码起不来：%s: %s" % (type(e).__name__, str(e)[:300]))

    step(0.60, "加载 SANA 模型（首次约 4 s）")
    try:
        kconfig.warmup()
    except Exception as e:
        die(2, "模型加载失败：%s: %s" % (type(e).__name__, str(e)[:400]))
    step(0.85, "模型就绪")

    def ready(real_port):
        log("服务已绑定 http://127.0.0.1:%d/（启动耗时 %.1fs）" % (real_port, time.time() - t0))
        emit("PIPE:READY port=%d" % real_port)

    try:
        server.serve(on_ready=ready)
    except OSError as e:
        die(2, "端口 %d 绑不上：%s" % (kconfig.PORT, e))
    log("已停止")


if __name__ == "__main__":
    main()
