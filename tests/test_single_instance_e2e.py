# -*- coding: utf-8 -*-
"""端到端：真的开两个启动器进程，确认第二个不会多开。

1. 起第一个（pythonw，真 GUI）→ 它必须拿到互斥体并写下 work/launcher.lock
2. 第二个带 NEOFORGE_SILENT=1 起 → 必须自己退场，并说出第一个的 pid
3. 杀掉第一个 → 互斥体随进程释放，必须能再抢到

注意：桌面上可能已经开着一个人家手动开的旧启动器窗口，所以这里不去数窗口，
只认互斥体 + 锁文件这一对硬证据（窗口标题可能撞车，不是可靠判据）。
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import singleton  # noqa: E402
import launcher.neoforge_launcher as L  # noqa: E402

LAUNCHER = os.path.join(ROOT, "launcher", "neoforge_launcher.py")
PYW = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
BASE = L.BASE_DIR                      # 便携包/仓库两种布局都由壳自己算，别在这里猜
LOCK = os.path.join(L.WORK_DIR, "launcher.lock")


def wait_lock_pid(pid, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        info = singleton.read_lock(LOCK)
        if info and info.get("pid") == pid:
            return info
        time.sleep(0.3)
    return None


def can_acquire(timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        g = singleton.acquire(BASE, lock_path=LOCK)
        if g is not None:
            return g
        time.sleep(0.4)
    return None


def main():
    print(f"pythonw={PYW}")
    print(f"BASE={BASE}  LOCK={LOCK}")
    assert os.path.isfile(PYW), "找不到 pythonw.exe"
    assert os.path.isfile(LAUNCHER), "找不到启动器脚本"
    pre = singleton.read_lock(LOCK)
    if pre:
        print(f"注意：开跑前已有锁文件 {pre}（可能是上次没清）")

    # ---- 1. 第一个
    first = subprocess.Popen([PYW, LAUNCHER], cwd=ROOT)
    info = wait_lock_pid(first.pid)
    assert info, f"第一个启动器没写下锁文件（pid={first.pid}）"
    print(f"① 第一个启动器拿到互斥体：{info['mutex']} · pid={info['pid']}")

    # ---- 2. 第二个：必须自己退场，并点名第一个
    env = dict(os.environ, NEOFORGE_SILENT="1")
    second = subprocess.run([sys.executable, LAUNCHER], cwd=ROOT, env=env,
                            capture_output=True, timeout=90)
    out = (second.stdout or b"").decode("utf-8", "replace") + \
          (second.stderr or b"").decode("utf-8", "replace")
    tail = out.strip().splitlines()[-1] if out.strip() else "(无输出)"
    print(f"② 第二个退出码={second.returncode}：{tail}")
    assert second.returncode == 0, f"第二个退出码不对：{second.returncode}"
    assert "已在运行" in out, f"第二个没说清为什么退场：{out!r}"
    assert f"pid {first.pid}" in out, f"第二个没点名第一个的 pid：{out!r}"
    still = singleton.read_lock(LOCK)
    assert still and still.get("pid") == first.pid, "第二个把锁抢走了（不该）"
    print("   锁仍在第一个手上 ✓")

    # ---- 3. 杀掉第一个 → 互斥体必须释放
    subprocess.run(["taskkill", "/PID", str(first.pid), "/T", "/F"],
                   capture_output=True, timeout=30)
    time.sleep(0.5)
    g = can_acquire()
    assert g is not None, "杀掉旧的之后抢不到互斥体 —— 锁没释放"
    print(f"③ 第一个已退出，互斥体重新可用：{g.name}")
    g.release()
    try:
        os.remove(LOCK)
    except OSError:
        pass
    print("E2E OK · 一个安装只开一个启动器")


if __name__ == "__main__":
    main()
