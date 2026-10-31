# -*- coding: utf-8 -*-
"""M3 验收：把 E:/NeoForge/backend 当内核登记进来，壳能起、能判活、能停、不留孤儿。

真起真停（会拉起 SD 后端，约 2–3 分钟、占显存）。跑完不留任何进程。
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import launcher.neoforge_launcher as L  # noqa: E402
from core import pipe  # noqa: E402

LOG = []


def log(msg):
    LOG.append(msg)
    print(msg, flush=True)


def python_leftovers():
    """还有没有 backend python 在跑 launch.py / image_app.py（孤儿检查）。"""
    import psutil
    out = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cl = " ".join(p.info.get("cmdline") or [])
        except Exception:
            continue
        if "python.exe" in (p.info.get("name") or "").lower() and (
                "launch.py" in cl or "image_app.py" in cl):
            out.append((p.info["pid"], cl[:90]))
    return out


def main():
    manifests, bad = L.discover_all()
    log(f"登记内核：{list(manifests)} 坏件={bad}")
    m = manifests["a1111"]
    log(f"a1111 → cmd={' '.join(m['command'])} cwd={m['cwd']} port={m['port']}")

    before = python_leftovers()
    log(f"启动前残留 python 进程：{before}")
    assert not before, "启动前就有残留，测试不干净，先手工清一下"

    r = pipe.KernelRunner(m, log=log)
    assert r.port_free(), f"端口 {m['port']} 已被占用"

    t0 = time.time()
    ok = r.start(wait_ready=300)
    ready_s = time.time() - t0
    if not ok:
        log(f"✗ 起不来：{r.last_error}")
        log("\n".join(LOG[-25:]))
        sys.exit(1)
    log(f"✓ 就绪 {ready_s:.1f}s，状态={r.state}，pid={r.proc.pid}")

    assert r.health_once(), "健康检查 /api/status 失败"
    log("✓ 健康检查通过")

    import socket
    for port in (8899, 7860):
        with socket.socket() as s:
            s.settimeout(1)
            up = s.connect_ex(("127.0.0.1", port)) == 0
        log(f"  端口 {port} {'LISTEN' if up else '未监听'}")

    time.sleep(3)
    tree = r._collect_pids()
    log(f"进程树：{tree}")

    clean, alive = r.stop()
    log(f"收尸结果：clean={clean} 残留={alive}")
    assert clean, f"收尸不干净：{alive}"

    time.sleep(2)
    left = python_leftovers()
    log(f"停止后残留 python 进程：{left}")
    for port in (8899, 7860):
        with socket.socket() as s:
            s.settimeout(1)
            up = s.connect_ex(("127.0.0.1", port)) == 0
        log(f"  端口 {port} {'仍占用 ✗' if up else '已释放 ✓'}")
        assert not up, f"端口 {port} 没释放"
    assert not left, f"有孤儿进程：{left}"
    log(f"M3 OK：起 {ready_s:.1f}s / 健康通过 / 收尸干净 / 端口全释放 / 零孤儿")


if __name__ == "__main__":
    main()
