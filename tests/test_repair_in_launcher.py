# -*- coding: utf-8 -*-
"""端到端：维修跑在启动器窗口里（pythonw + --repair），屏幕上不冒 cmd 黑框。

做法：真起 pythonw 启动器带 --repair，盯 launcher.log 等「维修结论」，
      同时每 0.3 s 数一次可见控制台窗口；跑完杀掉进程。

用法：python tests/test_repair_in_launcher.py
"""
import io
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import winproc  # noqa: E402

PYW = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
LAUNCHER = os.path.join(ROOT, "launcher", "neoforge_launcher.py")
LOG = os.path.join(os.path.dirname(ROOT), "work", "launcher.log")


def main():
    print(f"pythonw={PYW}")
    assert os.path.isfile(PYW), "找不到 pythonw.exe"
    base = set(winproc.console_windows())
    start_pos = os.path.getsize(LOG) if os.path.exists(LOG) else 0
    print(f"基线控制台窗口 {len(base)} 个；日志起点 {start_pos}")

    env = dict(os.environ, NEOFORGE_SILENT="1")   # 不弹「现在打开报告吗」
    p = subprocess.Popen([PYW, LAUNCHER, "--repair"], cwd=ROOT, env=env)
    print(f"已起：pythonw … --repair pid={p.pid}")

    seen, verdict = set(), None
    t0 = time.time()
    try:
        while time.time() - t0 < 120:
            seen.update(winproc.console_windows())
            if os.path.exists(LOG) and os.path.getsize(LOG) > start_pos:
                with io.open(LOG, encoding="utf-8", errors="replace") as f:
                    f.seek(start_pos)
                    tail = f.read()
                if "维修结论" in tail:
                    verdict = tail
                    break
            if p.poll() is not None:
                print("启动器提前退出，码", p.returncode)
                break
            time.sleep(0.3)
    finally:
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                       capture_output=True, timeout=30, **winproc.no_window_kwargs())

    new_consoles = sorted(seen - base)
    if verdict:
        print("--- 窗口里那几行 ---")
        for line in [l for l in verdict.splitlines() if l.strip()][-8:]:
            print("  " + line)
    print(f"期间新增控制台窗口：{len(new_consoles)} 个")
    assert verdict, "120s 内没看到维修结论，--repair 没跑起来"
    assert "--repair：启动即跑一次维修" in verdict, "没走 --repair 分支"
    assert not new_consoles, f"冒出控制台窗口：{new_consoles}"
    print("\nE2E OK · --repair 全在窗口内完成，无 cmd 黑框")


if __name__ == "__main__":
    main()
