# -*- coding: utf-8 -*-
"""单实例守卫验收：一个壳只能一个窗口，一个端口只能一个核。

真跑，不 mock：
  - 互斥体：抢两次，第二次必须拿不到；释放后能再抢
  - 端口：起一个真在 listen 的子进程，必须能报出它的 pid，杀掉后端口必须放
  - 管道：端口被占时 KernelRunner.start() 必须拒绝启动（而不是悄悄起第二份）
"""
import os
import socket
import subprocess
import sys
import time
import tkinter as tk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import kernel, pipe, singleton  # noqa: E402

LOCK = os.path.join(ROOT, "work", "test-launcher.lock")


def show(title):
    print(f"\n=== {title} ===")


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_listener(port):
    code = (
        "import socket,time\n"
        "s=socket.socket()\n"
        "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)\n"
        f"s.bind(('127.0.0.1',{port}))\n"
        "s.listen(5)\n"
        "time.sleep(120)\n"
    )
    p = subprocess.Popen([sys.executable, "-c", code])
    deadline = time.time() + 15
    while time.time() < deadline:
        if singleton.port_listening(port):
            return p
        time.sleep(0.1)
    p.kill()
    raise AssertionError(f"监听子进程没能在端口 {port} 上就位")


def test_mutex():
    show("互斥体：同一安装只能有一个启动器")
    a = singleton.acquire(ROOT, lock_path=LOCK)
    assert a is not None, "第一次抢不到互斥体"
    print(f"  第一次：{a.name}")
    b = singleton.acquire(ROOT, lock_path=LOCK)
    assert b is None, "第二次也抢到了 —— 会多开一个窗口"
    print("  第二次：被拦住（返回 None）✓")
    lock = singleton.read_lock(LOCK)
    assert lock and lock["pid"] == os.getpid(), f"锁文件不对：{lock}"
    a.release()
    c = singleton.acquire(ROOT, lock_path=LOCK)
    assert c is not None, "释放后抢不回来"
    print("  释放后：可再抢 ✓")
    c.release()
    d = singleton.acquire(ROOT + "-另一个安装", lock_path=LOCK + ".2")
    assert d is not None, "另一份安装被误伤"
    print("  另一份安装：互不影响 ✓")
    d.release()
    for p in (LOCK, LOCK + ".2"):
        try:
            os.remove(p)
        except OSError:
            pass


def test_window_focus():
    show("窗口：第二个启动器能把已有窗口叫到前面")
    win = tk.Tk()
    win.title("NeoForgeSD 1.3.0 自测窗口")
    win.geometry("240x120+40+40")
    win.update()
    hwnd = singleton.find_window("NeoForgeSD")
    assert hwnd, "找不到自己刚建的窗口"
    ok = singleton.focus_window("NeoForgeSD")
    print(f"  hwnd={hwnd} 聚焦={ok}")
    win.destroy()
    assert singleton.find_window("NeoForgeSD 1.3.0 自测窗口") is None, "窗口销毁后还能找到"
    print("  销毁后找不到（正常）✓")


def test_port_owner():
    show("端口：报出占用者 pid，杀干净后端口释放")
    port = free_port()
    child = start_listener(port)
    try:
        owner = singleton.port_owner(port)
        assert owner, f"端口 {port} 有人听却报不出占用者"
        print(f"  {port} → {singleton.owner_line(owner)}")
        assert owner["pid"] == child.pid, f"pid 对不上：{owner['pid']} != {child.pid}"
        alive = singleton.kill_tree(child.pid)
        assert not alive, f"杀完还活着：{alive}"
        assert singleton.wait_free(port, 10), "杀完了端口还在听"
        print("  收尸后端口已释放 ✓")
    finally:
        if child.poll() is None:
            child.kill()


def test_runner_refuses_second():
    show("管道：端口被占时拒绝起第二份")
    port = free_port()
    child = start_listener(port)
    try:
        found = dict((m["id"], m) for m, err in kernel.discover(ROOT) if m)
        fake = dict(found["fake"])
        fake["port"] = port
        fake["command"] = list(fake["command"])
        logs = []
        r = pipe.KernelRunner(fake, log=logs.append)
        ok = r.start(wait_ready=3)
        print(f"  start() 返回 {ok}；last_error={r.last_error}")
        assert ok is False, "端口被占还启动了 —— 会起第二份"
        assert "占用" in r.last_error, f"错误信息没说清是被占：{r.last_error}"
        assert r.proc is None, "流程里留下了子进程句柄"
        assert any("占用" in s for s in logs), "日志里没记这一笔"
        print("  拒绝启动 + 说清是谁占的 ✓")
    finally:
        if child.poll() is None:
            child.kill()


def main():
    print(f"解释器={sys.executable}")
    print(f"单实例模块={'Windows 互斥体' if singleton.IS_WIN else '锁文件'}")
    test_mutex()
    test_window_focus()
    test_port_owner()
    test_runner_refuses_second()
    print("\nSMOKE OK · 单实例守卫")


if __name__ == "__main__":
    main()
