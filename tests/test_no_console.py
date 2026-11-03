# -*- coding: utf-8 -*-
"""验收：启动器与维修全程不弹 cmd 黑框。

两段：
  1. 静态：core/ + launcher/ + ui/ 里不许再直接 subprocess.run/Popen（必须走 core.winproc）
     —— 例外只有 ui/stop.py（命令行小工具，本来就是给自己开控制台用的）
  2. 动态：用 pythonw（无控制台）真跑一遍检测 / 维修预演 / 假内核起停 / 几条控制台命令，
     期间每 40 ms 数一次屏幕上可见的控制台窗口；开跑前后的集合必须一模一样（一个都不多）。

用法：python tests/test_no_console.py
      内部会把自己以 pythonw 重开一遍跑 --child（因为测试进程自己有控制台，看不清黑框）。
"""
import json
import os
import re
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "work", "_no_console.json")
PYW = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")

ALLOW_SUBPROCESS = {os.path.join("ui", "stop.py")}


# ------------------------------------------------------------------ 静态
def scan_static():
    bad = []
    for sub in ("core", "launcher", "ui"):
        base = os.path.join(ROOT, sub)
        for dirpath, _dirs, files in os.walk(base):
            for f in files:
                if not f.endswith(".py"):
                    continue
                p = os.path.join(dirpath, f)
                rel = os.path.relpath(p, ROOT)
                if rel == os.path.join("core", "winproc.py") or rel in ALLOW_SUBPROCESS:
                    continue
                try:
                    src = open(p, encoding="utf-8").read()
                except OSError:
                    continue
                for m in re.finditer(r"subprocess\.(run|Popen)\(", src):
                    line = src[: m.start()].count("\n") + 1
                    bad.append(f"{rel}:{line}")
    return bad


# ------------------------------------------------------------------ 动态（子进程里跑）
def child():
    from core import detect, kernel, pipe, winproc

    baseline = set(winproc.console_windows())
    seen = set()
    stop = threading.Event()

    def watch():
        while not stop.is_set():
            seen.update(winproc.console_windows())
            time.sleep(0.04)

    t = threading.Thread(target=watch, daemon=True)
    t.start()

    ops, err = [], ""
    try:
        t0 = time.time()
        items = detect.run_checks(ROOT and os.path.dirname(ROOT))
        ops.append(f"检测六项 {len(items)} 项 · {time.time() - t0:.2f}s")

        from core import repair
        base = os.path.dirname(ROOT)
        t0 = time.time()
        repair.run(base, dry=True)
        ops.append(f"维修预演 · {time.time() - t0:.2f}s")

        found = dict((m["id"], m) for m, _e in kernel.discover(ROOT) if m)
        fake = dict(found["fake"])
        logs = []
        r = pipe.KernelRunner(fake, log=logs.append)
        t0 = time.time()
        ok = r.start(wait_ready=20)
        clean, alive = r.stop()
        ops.append(f"假内核起停 ok={ok} 干净={clean} · {time.time() - t0:.2f}s")

        # 直接跑几条控制台程序，确认它们不建窗口（这条最容易闪黑框）
        for cmd in (["tasklist", "/FI", "PID eq 0"], ["netstat", "-ano", "-p", "TCP"]):
            winproc.run(cmd, timeout=20)
        ops.append("tasklist / netstat 静音执行")
    except Exception as e:                    # 子进程里出事要带回去，不能静默
        import traceback
        err = traceback.format_exc()
    finally:
        stop.set()
        t.join(timeout=2)

    delta = sorted(seen - baseline)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"delta": delta, "baseline": sorted(baseline), "ops": ops, "error": err},
                  f, ensure_ascii=False, indent=2)


# ------------------------------------------------------------------ 主控
def main():
    print(f"解释器={sys.executable}")
    bad = scan_static()
    print(f"\n静态扫描：直接调 subprocess 的地方 {len(bad)} 处")
    for b in bad:
        print("  ✗", b)
    assert not bad, "还有地方没走 core.winproc，运行时会闪黑框"

    assert os.path.isfile(PYW), f"找不到 pythonw：{PYW}"
    if os.path.exists(OUT):
        os.remove(OUT)
    print(f"\n动态：用 pythonw 重开自己（{os.path.basename(PYW)} --child）跑真实操作")
    child_proc = subprocess.Popen([PYW, os.path.abspath(__file__), "--child"], cwd=ROOT)
    t0 = time.time()
    while time.time() - t0 < 180:
        if os.path.exists(OUT):
            time.sleep(0.8)                     # 等它写完
            break
        if child_proc.poll() is not None:
            break
        time.sleep(0.3)
    if child_proc.poll() is None:
        child_proc.kill()
    assert os.path.exists(OUT), "pythonw 子进程没交回结果"
    data = json.load(open(OUT, encoding="utf-8"))
    for line in data["ops"]:
        print("  ✓", line)
    print(f"  屏幕上的控制台窗口：基线 {len(data['baseline'])} 个，期间新增 {len(data['delta'])} 个")
    if data["error"]:
        print(data["error"])
        raise AssertionError("子进程里出错了")
    assert not data["delta"], f"跑的过程中冒出控制台窗口：{data['delta']}"
    print("\nNO-CONSOLE OK · 检测/维修/起停全程没有 cmd 黑框")


if __name__ == "__main__":
    if "--child" in sys.argv:
        child()
    else:
        main()
