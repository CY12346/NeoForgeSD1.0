#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""NeoForgeSD 内核入口 · neoforge-sd15

壳（或人）这样起它：  {python} {kernel}/kernel_main.py

本文件只干三件事，一行业务代码都不该长在这里：
  1. 把 SD_HOME / 工作目录 / 端口从环境变量里拿稳；缺东西**说人话然后退出**
  2. 按管道协议在 stdout 打 PIPE: 行
  3. import neoforge.server 并起服务（分层见 neoforge/__init__.py）

自己一个人跑也可以：不带环境变量时会从内核目录往上找 backend/python/python.exe（最多五层）。

退出码：0 正常停止 ｜ 2 端口占用/启动失败 ｜ 3 环境不全
"""
import json
import os
import sys
import time

KERNEL = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(KERNEL, "kernel.json")
PKG = os.path.join(KERNEL, "neoforge")


def emit(line):
    """管道行：立刻 flush，壳是逐行读的，不能攒在缓冲区里。"""
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


def find_base():
    cur = KERNEL
    for _ in range(5):
        if os.path.isfile(os.path.join(cur, "backend", "python", "python.exe")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return ""


def check_env(m):
    """把环境装配好；缺什么直接退出，不静默兜底。返回 (sd_home, work, port)。"""
    sd_home = os.environ.get("NEOFORGE_SD_HOME") or ""
    if not sd_home:
        base = find_base()
        if base:
            sd_home = os.path.join(base, "backend")
            os.environ["NEOFORGE_SD_HOME"] = sd_home
            log("没给 NEOFORGE_SD_HOME，按内核位置推出来：%s" % sd_home)
    if not sd_home or not os.path.isdir(sd_home):
        die(3, "找不到 SD 后端目录（NEOFORGE_SD_HOME=%r）。这个内核不自带后端，"
               "需要一份带 python/ 的 A1111/Forge 目录，见 KERNEL-neoforge-sd15.md §3" % sd_home)

    sd_py = os.path.join(sd_home, "python", "python.exe")
    if not os.path.isfile(sd_py):
        die(3, "SD 后端里没有 python：%s。内核要的是「自带 python 的整合包」，"
               "光有源码不行（见 KERNEL-neoforge-sd15.md §3）" % sd_py)

    work = os.environ.get("NEOFORGE_IMAGE_WORK") or ""
    if not work:
        base = find_base() or os.path.dirname(KERNEL)
        work = os.path.join(base, "work")
        os.environ["NEOFORGE_IMAGE_WORK"] = work
    os.makedirs(work, exist_ok=True)

    port = int(os.environ.get("NEOFORGE_IMAGE_PORT") or m.get("port") or 8899)
    os.environ.setdefault("NEOFORGE_IMAGE_PORT", str(port))
    return sd_home, work, port


def main():
    t0 = time.time()
    m = manifest()
    log("neoforge-sd15 内核 v%s 启动（%s）" % (m.get("version", "?"), KERNEL))

    step(0.10, "检查环境")
    sd_home, work, port = check_env(m)
    log("SD_HOME=%s · WORK=%s · 端口=%d" % (sd_home, work, port))
    step(0.30, "环境就绪（后端 python 已确认）")

    # 代码完整性：分层的每一层都得在。缺了就说明这个内核目录是半成品。
    missing = [p for p in ("config.py", "sdapi.py", "engine.py", "payload.py",
                           "jobs.py", "history.py", "textfmt.py", "server.py",
                           "ui/image_page.html")
               if not os.path.isfile(os.path.join(PKG, p.replace("/", os.sep)))]
    if missing:
        die(3, "内核代码不完整，缺：%s。在仓库里跑 `python tools/build_kernel.py` 先构建"
               % "、".join(missing))

    if KERNEL not in sys.path:
        sys.path.insert(0, KERNEL)
    step(0.55, "载入内核代码")
    try:
        from neoforge import server                                  # noqa: E402
        from neoforge import config as kconfig                       # noqa: E402
    except Exception as e:
        die(2, "内核代码起不来：%s: %s" % (type(e).__name__, str(e)[:300]))

    def ready(real_port):
        log("服务已绑定 http://127.0.0.1:%d/（启动耗时 %.1fs）" % (real_port, time.time() - t0))
        emit("PIPE:READY port=%d" % real_port)

    step(0.75, "绑定端口")
    try:
        server.serve(on_ready=ready)
    except OSError as e:
        die(2, "端口 %d 绑不上：%s（可能上一次的内核没退干净，或被别的程序占了）"
               % (kconfig.PORT, e))
    log("已停止")


if __name__ == "__main__":
    main()
