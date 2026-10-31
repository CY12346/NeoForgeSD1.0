# -*- coding: utf-8 -*-
"""M1 验收：假内核的管道能不能起、判活、报进度、收尸、不留孤儿。

真跑，不 mock 子进程。只用可替换的 check 函数造故障（M2 那三种）。
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import detect, kernel, pipe  # noqa: E402

LOG = []


def log(msg):
    LOG.append(msg)


def show(title):
    print(f"\n=== {title} ===")


def test_discover():
    show("登记册：扫 kernels/")
    found = kernel.discover(ROOT)
    assert found, "一个 kernel.json 都没扫到"
    ok = {m["id"] for m, err in found if m}
    bad = {err for m, err in found if err}
    print("  可用:", sorted(ok))
    for b in bad:
        print("  坏件:", b)
    assert "fake" in ok, "kernels/fake 没被识别"
    assert "a1111" in ok, "kernels/a1111 没被识别"
    return ok


def test_manifest_errors():
    show("登记册：坏 json 要报人话错误，不许静默")
    cases = [
        ('{"spec": 9, "id": "x", "name": "x", "command": ["a"], "port": 1}', "spec"),
        ('{"spec": 1, "id": "wrong", "name": "x", "command": ["a"], "port": 1}', "目录名"),
        ('{"spec": 1, "id": "x", "name": "x", "command": [], "port": 1}', "command"),
        ('{"spec": 1, "id": "x", "name": "x", "command": ["a"], "port": 70000}', "port"),
    ]
    tmpdir = os.path.join(ROOT, "work", "_manifest_probe", "x")
    os.makedirs(tmpdir, exist_ok=True)
    mf = os.path.join(tmpdir, "kernel.json")
    for body, word in cases:
        with open(mf, "w", encoding="utf-8") as f:
            f.write(body)
        try:
            kernel.load_manifest(mf, base_dir=ROOT)
            raise AssertionError(f"这份应该被拒：{body}")
        except kernel.ManifestError as e:
            assert word in str(e), f"错误信息里该提「{word}」，实际：{e}"
            print(f"  ✓ 拒绝：{str(e)[:70]}")
    os.remove(mf)


def test_pipe_roundtrip():
    show("管道：起 → 判活 → 进度 → 收尸")
    manifests = {m["id"]: m for m, err in kernel.discover(ROOT) if m}
    m = manifests["fake"]
    progresses = []
    states = []
    r = pipe.KernelRunner(
        m, log=log,
        on_progress=lambda f, t: progresses.append((f, t)),
        on_state=lambda s, d: states.append(s),
    )
    assert r.port_free(), "启动前端口就被占了，测试不干净"

    t0 = time.time()
    ok = r.start(wait_ready=40)
    ready_s = time.time() - t0
    assert ok, f"假内核没起来：{r.last_error}\n" + "\n".join(LOG[-15:])
    assert r.state == pipe.GREEN, f"状态应为 green，实际 {r.state}"
    assert r.health_once(), "健康检查失败"
    print(f"  ✓ 就绪 {ready_s:.1f}s，状态 {r.state}，pid={r.proc.pid}")

    time.sleep(1.2)
    assert progresses, "没收到任何 PIPE:PROGRESS"
    assert any(f > 0 for f, _ in progresses), f"进度值不对：{progresses}"
    print(f"  ✓ 进度事件 {len(progresses)} 条，最后 {progresses[-1][0]:.0%} {progresses[-1][1]}")

    tree = r._collect_pids()
    clean, alive = r.stop()
    assert clean, f"收尸不干净，残留 {alive}"
    assert r.port_free(), "停止后端口仍被占用"
    assert r.state == pipe.STOPPED, f"状态应为 stopped，实际 {r.state}"
    print(f"  ✓ 收尸干净：进程树 {tree} 全清，端口 {m['port']} 释放")

    for pid in tree:
        assert not pipe._alive(pid), f"pid {pid} 还活着（孤儿）"
    return progresses


def test_stop_when_idle():
    show("管道：没起就停，不许炸")
    manifests = {m["id"]: m for m, err in kernel.discover(ROOT) if m}
    r = pipe.KernelRunner(manifests["fake"], log=log)
    clean, alive = r.stop()
    assert clean and not alive, "空转停止应返回干净"
    print("  ✓ idle 状态下 stop() 返回干净")


def test_detect_three_faults():
    show("侦探：三种故障都要认出来（端口占用 / models 缺失 / 显存不足）")
    base = os.path.join(ROOT, "work", "_detect_probe")
    os.makedirs(base, exist_ok=True)

    # 故障 1：端口被占（真开一个 socket 占着 8899）
    import socket
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(("127.0.0.1", 8899))
        srv.listen(1)
        item = detect.check_port(port=8899)
        assert item["level"] != detect.GREEN and "占用" in item["detail"], item
        print(f"  ✓ 端口占用 → {item['level']}：{item['detail']}")
    finally:
        srv.close()

    # 故障 2：models 缺失（指向一个空目录）
    empty = os.path.join(base, "no_models")
    os.makedirs(empty, exist_ok=True)
    item = detect.check_models(empty)
    assert item["level"] == detect.RED, item
    print(f"  ✓ 模型缺失 → {item['level']}：{item['detail'][:60]}")

    # 故障 3：显存不足 —— 用替换后的 check 造一个 2GB 卡的读数
    def fake_gpu(_base):
        return detect._item("gpu", "显卡", detect.YELLOW,
                            "FAKE 2.0 GB · 已用 0.5 GB", "显存低于 4 GB：用 512×512 出图")
    items = detect.run_checks(ROOT, checks=(detect.check_python, fake_gpu))
    gpu_item = [i for i in items if i["key"] == "gpu"][0]
    assert gpu_item["level"] == detect.YELLOW, gpu_item
    level, summary = detect.verdict(items)
    print(f"  ✓ 显存不足 → {gpu_item['level']}；整机结论 {level}：{summary[:70]}")
    return items


def main():
    ids = test_discover()
    test_manifest_errors()
    prog = test_pipe_roundtrip()
    test_stop_when_idle()
    items = test_detect_three_faults()

    show("真实环境侦探（本机）")
    for it in detect.run_checks(ROOT):
        print(f"  [{it['level']:6s}] {it['label']:10s} {it['detail']}")
        if it["fix"]:
            print(f"            → {it['fix']}")

    print("\n全部通过：登记册 %d 个内核 / 进度事件 %d 条 / 收尸干净 / 三种故障可识别"
          % (len(ids), len(prog)))


if __name__ == "__main__":
    main()
