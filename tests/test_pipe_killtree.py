# -*- coding: utf-8 -*-
"""收尸回归：内核自己拉起的子进程（A1111 后端那种），stop 必须一起清掉。

坑（2026-11-03 实测）：父进程 terminate 后在 Windows 上立刻消失，旧实现到那时候才去
`psutil.Process(self.proc.pid).children()` → 抛异常 → 空列表 → 后端进程活着占着 4 GB 显存。
这个测试用一个「自己 spawn 一个 sleep 子进程」的假内核守住它。

    python tests/test_pipe_killtree.py
"""
import json
import os
import shutil
import sys
import tempfile
import time

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
from core import kernel, pipe                     # noqa: E402

STUB = '''# -*- coding: utf-8 -*-
import subprocess, sys
print("PIPE:READY port=%d" % PORT, flush=True)
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                         creationflags=0x08000000)
print("CHILD %d" % child.pid, flush=True)
import time
time.sleep(120)
'''


def main():
    tmp = tempfile.mkdtemp(prefix="neoforge-killtree-")
    kdir = os.path.join(tmp, "kernels", "stub")
    os.makedirs(kdir)
    port = 18923
    with open(os.path.join(kdir, "spawner.py"), "w", encoding="utf-8") as f:
        f.write("PORT = %d\n" % port + STUB)
    man = {
        "spec": 1, "id": "stub", "name": "收尸回归桩", "version": "0.0.1", "dialect": "custom",
        "command": [sys.executable, "{kernel}/spawner.py"], "cwd": "{kernel}", "args": [], "env": {},
        "port": port,
        "health": {"path": "/healthz", "interval_s": 0.5, "timeout_s": 1.0, "fail_threshold": 3},
        "ready": {"mode": "line", "pattern": r"PIPE:READY port=(\d+)"},
        "progress": {"pattern": r"PIPE:PROGRESS ([0-9.]+)\s*(.*)"},
        "needs": {}, "stop": {"grace_s": 2},
    }
    with open(os.path.join(kdir, "kernel.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False)

    logs = []
    m = kernel.load_manifest(os.path.join(kdir, "kernel.json"), base_dir=tmp)
    r = pipe.KernelRunner(m, log=logs.append)
    ok = r.start(wait_ready=20)
    print("  起桩：%s · pid=%s" % (ok, r.proc.pid if r.proc else None))

    child = None
    deadline = time.time() + 10
    while child is None and time.time() < deadline:
        for line in logs:
            if line.startswith("CHILD "):
                child = int(line.split()[1])
                break
        if child is None:
            time.sleep(0.2)
    print("  桩拉起的子进程：%s" % child)

    import psutil
    if not (ok and child and psutil.pid_exists(child)):
        print("✗ 桩没起来或子进程没抓到")
        r.stop()
        shutil.rmtree(tmp, ignore_errors=True)
        return 1

    clean, alive = r.stop()
    time.sleep(1)
    still = psutil.pid_exists(child)
    print("  stop → clean=%s alive=%s ｜ 子进程还在？%s" % (clean, alive, still))
    shutil.rmtree(tmp, ignore_errors=True)
    if still:
        try:
            psutil.Process(child).kill()
        except Exception:
            pass
        print("✗ KILLTREE FAIL：父进程停了，它拉起的子进程还活着（4 GB 显存就是这么被吃掉的）")
        return 1
    if not clean:
        print("✗ KILLTREE FAIL：stop 自报不干净 %s" % alive)
        return 1
    print("KILLTREE OK · stop 把内核拉起的子进程一起收掉，自报干净")
    return 0


if __name__ == "__main__":
    sys.exit(main())
