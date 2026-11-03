# -*- coding: utf-8 -*-
"""维修自测：造一个孤儿内核进程 → 维修要能清掉它 → 端口释放。

不碰 A1111，只用假内核（kernels/fake，18899）。
"""
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import repair, selfcheck  # noqa: E402


def port_listening(port, timeout=0.3):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex(("127.0.0.1", port)) == 0


def main():
    base = selfcheck.base_of(ROOT)
    print(f"base={base}")
    ms, bad = selfcheck._discover(base)
    fake = next((m for m in ms if m["id"] == "fake"), None)
    assert fake, "找不到假内核"
    port = fake["port"]
    assert not port_listening(port), f"端口 {port} 一开始就被占着，测不了"

    # 1) 造孤儿：脱离本进程会话启动，父进程不是启动器
    script = os.path.join(ROOT, "kernels", "fake", "fake_kernel.py")
    py = fake["command"][0]
    detach = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    proc = subprocess.Popen([py, script, str(port)], cwd=os.path.dirname(script),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=detach)
    for _ in range(40):
        if port_listening(port):
            break
        time.sleep(0.25)
    assert port_listening(port), "孤儿内核没起来，测试无效"
    print(f"孤儿内核已起：pid={proc.pid} port={port}")

    # 2) 预演不该动手
    actions, *_ = repair.run(base, dry=True)
    orphan_act = next(a for a in actions if a["kind"] == "清孤儿")
    assert "预演" in orphan_act["detail"], f"预演竟然动手了：{orphan_act}"
    assert port_listening(port), "预演把进程干掉了，这是 bug"
    print(f"预演正确未动手：{orphan_act['detail']}")

    # 3) 真修，应该清掉
    actions, items, level, summary = repair.run(base, dry=False)
    orphan_act = next(a for a in actions if a["kind"] == "清孤儿")
    print(f"真修：{orphan_act['detail']}")
    assert str(proc.pid) in orphan_act["detail"], "没清掉我们造的那个孤儿"

    for _ in range(20):
        if not port_listening(port):
            break
        time.sleep(0.25)
    assert not port_listening(port), f"端口 {port} 还占着"
    print(f"端口 {port} 已释放")
    print(f"复检结论：{summary}")
    print("REPAIR OK")


if __name__ == "__main__":
    main()
