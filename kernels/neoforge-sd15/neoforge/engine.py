# -*- coding: utf-8 -*-
"""引擎层：SD 后端进程的生命周期（起 / 判活 / 停 / 日志 / 启动参数）。

换后端方言（ComfyUI、自研）时要改的就是这个文件 + sdapi.py 的路径，上层不动。

4 GB 卡（RTX3050 Laptop）实测参数，改之前先看注释：
  · 不用 --medvram：block swap 让 1024² 慢 18%，而 4 GB 上本就跑得下
  · 用 --xformers 而不是 --opt-sdp-attention：sdp 在 sm86 上更慢（实测 xformers 快 ~18%）
  · 默认不开 --no-half-vae：VAE 常驻显存 2375MB → 3007MB，1024² 从 38.6s 拖到 64s；
    本机 vae-ft-mse 在 fp16 下与 fp32 逐像素一致（max_diff=0.0）
"""
import os
import threading
import time

from . import config, sdapi
from ._vendor import winproc

# 后端生命周期（内核自己拉起 SD，headless：--nowebui，不用再开 7860 的网页）
START = {"state": "idle", "since": 0.0, "msg": "", "pid": None}
START_LOCK = threading.Lock()

SD_ARGS = ["--api", "--nowebui", "--port", "7860",
           "--xformers", "--skip-version-check", "--skip-install"]
if os.environ.get("NEOFORGE_INSTALL_REQS") == "1":
    SD_ARGS.remove("--skip-install")
if os.environ.get("NEOFORGE_SD_MEDVRAM") == "1":
    SD_ARGS.insert(4, "--medvram")
if os.environ.get("NEOFORGE_SAFE_VAE") == "1":
    SD_ARGS.append("--no-half-vae")


def args():
    return list(SD_ARGS)


def status():
    st = dict(START)
    st["args"] = list(SD_ARGS)
    st["python"] = config.SD_PY
    if st["state"] == "starting":
        st["elapsed"] = round(time.time() - st["since"], 1)
    return st


def _boot():
    # expandable_segments：PyTorch 2.1+ 的显存分配器选项，专治 4 GB 卡「明明够却 OOM」的碎片问题。
    # 必须在子进程 CUDA 初始化前注入，所以走 env 而不是启动后再设。
    env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", PYTHONUTF8="1",
               PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True")
    log = config.SD_LOG
    f = open(log, "ab")
    proc = None
    try:
        proc = winproc.popen([config.SD_PY, "webui.py"] + SD_ARGS, cwd=config.SD_HOME, env=env,
                             stdout=f, stderr=f)
        START["pid"] = proc.pid
    except Exception as e:
        START.update(state="failed", msg=f"拉起失败：{e}")
        return
    for _ in range(240):  # 最多 8 分钟（首次加载模型很慢）
        time.sleep(2)
        if sdapi.alive():
            sdapi.invalidate()
            START.update(state="ready", msg="已启动")
            return
        if proc.poll() is not None:
            START.update(state="failed", msg=f"进程退出（码 {proc.returncode}），日志：{log}")
            return
    START.update(state="failed", msg=f"启动超时，日志：{log}")


def start():
    """不阻塞：立刻返回，页面轮询 /api/status 看进度。"""
    if sdapi.alive():
        START.update(state="ready", msg="已经在跑")
        return True, "后端已经在跑"
    if not os.path.isfile(config.SD_PY):
        START.update(state="failed", msg=f"找不到 python：{config.SD_PY}")
        return False, f"找不到 python：{config.SD_PY}"
    with START_LOCK:
        if START["state"] == "starting":
            return True, "已经在启动中"
        START.update(state="starting", since=time.time(), msg="启动中")
    threading.Thread(target=_boot, daemon=True).start()
    return True, "开始启动，约 60–120 秒（首次加载模型更久）"


def stop():
    """结束后端、释放显存。只杀监听 7860 的进程或本服务拉起的那个。"""
    pids = set()
    if START.get("pid"):
        pids.add(int(START["pid"]))
    try:
        raw = winproc.run(["netstat", "-ano"], capture_output=True, timeout=20).stdout
        out = raw.decode("gbk", "replace")   # netstat 在本机是 GBK；text=True 会被 PYTHONUTF8 带偏
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
            winproc.run(["taskkill", "/PID", str(pid), "/F", "/T"],
                        capture_output=True, timeout=20)
            killed.append(pid)
        except Exception:
            pass
    START.update(state="idle", pid=None, msg="已停止")
    sdapi.invalidate()
    if killed:
        return True, "已结束进程 " + ", ".join(str(k) for k in killed)
    return False, "没找到可结束的进程"
