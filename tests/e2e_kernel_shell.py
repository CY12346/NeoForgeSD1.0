# -*- coding: utf-8 -*-
"""端到端：壳的工具链（core/pipe.KernelRunner）→ 真内核 neoforge-sd15 → 真出图 → 收尸。

不是 mock，全走真进程真 HTTP：
    起核(收 PIPE:READY) → /api/start_sd 起 A1111 后端 → /api/image 下单
    → 轮询 /api/job/<id> → 拿到图 → 停核 → 端口释放、无孤儿

    E:/NeoForge/backend/python/python.exe tests/e2e_kernel_shell.py
"""
import json
import os
import socket
import sys
import time
import urllib.request

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 真正的安装根不是 app/：从 app/ 往上找带 backend/ 或 models/ 的那一层（和启动器 _find_base 同一套判断）
ROOT = APP
for _ in range(3):
    if os.path.isdir(os.path.join(ROOT, "backend")) or os.path.isdir(os.path.join(ROOT, "models")):
        break
    ROOT = os.path.dirname(ROOT)
sys.path.insert(0, ROOT)
sys.path.insert(0, APP)

from core import kernel, pipe          # noqa: E402

FAILED = []


def check(name, cond, extra=""):
    print(("  ✓ " if cond else "  ✗ ") + name + (f"  {extra}" if extra else ""), flush=True)
    if not cond:
        FAILED.append(name)
    return cond


def http(port, path, body=None, timeout=120):
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def port_busy(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def main():
    found = kernel.discover(APP, base_dir=ROOT)      # 内核在 app/kernels，落点根是 ROOT —— 和启动器同一套传法
    m = next((man for man, err in found if man and man.get("id") == "neoforge-sd15"), None)
    if not m:
        print("✗ 找不到 neoforge-sd15 内核")
        return 1
    port = m["port"]
    print(f"内核 neoforge-sd15 · 端口 {port} · {m.get('command')}")

    states, logs, prog = [], [], []
    runner = pipe.KernelRunner(
        m, log=lambda s: (logs.append(s), print("    ·", s, flush=True)),
        on_progress=lambda f, t: prog.append((f, t)),
        on_state=lambda s, d: states.append((s, d)))

    t0 = time.time()
    print("\n[1] 起核（等 PIPE:READY）")
    ok = runner.start()
    t_ready = time.time() - t0
    check("核起来了", ok, f"{t_ready:.1f}s · state={runner.state}")
    if not ok:
        print("    last_error:", runner.last_error)
        return 1
    check("内核 HTTP 可访问", runner.health_once())
    check("收到 READY 状态", any(s in ("green", "ready", "ready_ok") for s, _ in states), str(states[-1:]))

    print("\n[2] 内核 → 后端（/api/start_sd）")
    t1 = time.time()
    try:
        r = http(port, "/api/start_sd", {})
        print("    →", r)
    except Exception as e:                        # noqa: BLE001
        print("    ✗ 起后端失败：", e)
        runner.stop()
        return 1
    # 后端起来了才能出图：轮询 /api/progress 里的 online
    online = False
    for _ in range(90):                           # 最长 180 s
        try:
            p = http(port, "/api/progress", timeout=10)
            if p.get("online") or p.get("progress") is not None and not p.get("error"):
                online = True
                break
        except Exception:                         # noqa: BLE001
            pass
        time.sleep(2)
    t_backend = time.time() - t1
    check("后端在线", online, f"{t_backend:.1f}s")

    print("\n[3] 出图（512² / 8 步，走壳的同一套 HTTP）")
    t2 = time.time()
    try:
        job = http(port, "/api/image", {
            "prompt": "a red panda barista, coffee shop, warm light",
            "negative": "blurry, low quality",
            "width": 512, "height": 512, "steps": 8, "cfg": 5.5,
            "sampler": "DPM++ 2M", "scheduler": "karras", "seed": 1234, "n": 1,
        })
    except Exception as e:                        # noqa: BLE001
        print("    ✗ 下单失败：", e)
        runner.stop()
        return 1
    jid = job.get("job_id")
    check("拿到 job_id", bool(jid), str(jid))

    res = None
    for _ in range(240):                          # 最长 480 s
        try:
            j = http(port, f"/api/job/{jid}", timeout=10)
        except Exception:                         # noqa: BLE001
            time.sleep(1)
            continue
        st = j.get("state")                       # jobs.py 的字段名是 state，不是 status
        if st in ("done", "error"):
            res = j
            break
        if int(time.time() - t2) % 15 == 0:
            print(f"    … {st}", flush=True)
        time.sleep(1)
    t_gen = time.time() - t2
    check("任务完成", bool(res) and res.get("state") == "done",
          f"{t_gen:.1f}s · {res.get('state') if res else 'timeout'}")

    files = []
    if res:
        files = res.get("files") or ([res["file"]] if res.get("file") else [])
    if not files:
        try:
            h = http(port, "/api/history?limit=3")
            items = h.get("items") or []
            files = [it.get("file") for it in items if it.get("file")][:1]
        except Exception:                         # noqa: BLE001
            pass
    if check("有产出文件", bool(files), str(files)[:120]):
        p = files[0]
        full = p if os.path.isabs(p) else os.path.join(ROOT, p)
        check("文件真的落盘", os.path.isfile(full),
              f"{full} {os.path.getsize(full)//1024 if os.path.isfile(full) else 0} KB")

    print("\n[4] 收尸")
    t3 = time.time()
    st = runner.stop()
    print("    stop() →", st)
    check("内核已停", not runner.proc or runner.proc.poll() is not None, f"{time.time()-t3:.1f}s")
    check("端口已释放", not port_busy(port), f":{port}")

    print("\n=== 汇总 ===")
    print(f"  起核 {t_ready:.1f}s · 后端起 {t_backend:.1f}s · 出图 {t_gen:.1f}s · 收尸 {time.time()-t3:.1f}s")
    if FAILED:
        print("失败：" + "，".join(FAILED))
        return 1
    print("E2E OK · 壳 → 核 → 后端 → 出图 → 收尸 全通")
    return 0


if __name__ == "__main__":
    sys.exit(main())
