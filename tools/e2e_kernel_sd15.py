# -*- coding: utf-8 -*-
"""neoforge-sd15 内核端到端出图：壳那套管道起核 → 拉起 SD 后端 → 真出一张 512² 图 → 全收干净。

    python tools/e2e_kernel_sd15.py [--steps 8]

这是「拆出来的内核真能用」的证据脚本，不是单元测试（要占 4 GB 显存、约 3 分钟）。
"""
import json
import os
import struct
import sys
import time
import urllib.request

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.dirname(APP)
sys.path.insert(0, APP)
from core import kernel, pipe                                    # noqa: E402

KDIR = os.path.join(APP, "kernels", "neoforge-sd15")


def req(path, payload=None, timeout=60):
    url = "http://127.0.0.1:%d%s" % (PORT, path)
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(26)
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


def main():
    global PORT
    steps = 8
    if "--steps" in sys.argv:
        steps = int(sys.argv[sys.argv.index("--steps") + 1])
    m = kernel.load_manifest(os.path.join(KDIR, "kernel.json"), base_dir=BASE)
    PORT = m["port"]
    logs = []
    r = pipe.KernelRunner(m, log=logs.append)
    t0 = time.time()
    if not r.start(wait_ready=90):
        print("✗ 内核起不来：%s" % r.last_error)
        return 1
    print("① 内核就绪 %.1fs（管道 / PIPE:READY）" % (time.time() - t0))

    try:
        print("② 让内核拉起 SD 后端（首启要加载模型，约 2–3 分钟）")
        try:
            req("/api/start_sd", {}, timeout=30)
        except Exception as e:
            print("   已经在线或启动失败：%s" % e)
        t1 = time.time()
        online = False
        while time.time() - t1 < 360:
            st = req("/api/status", timeout=20)
            if st.get("online"):
                online = True
                print("   SD 后端在线 %.1fs · 模型 %s" % (time.time() - t1, st.get("model")))
                break
            b = st.get("backend") or {}
            if b.get("state") == "failed":
                print("   ✗ 后端失败：%s" % b.get("msg"))
                return 1
            time.sleep(5)
        if not online:
            print("   ✗ 360s 内后端没上线")
            return 1

        print("③ 出一张图（512² · %d 步）" % steps)
        t2 = time.time()
        job = req("/api/image", {"prompt": "1girl, silver hair, armor, desert, sunset, masterpiece, best quality",
                                 "negative": "lowres, bad anatomy, blurry",
                                 "width": 512, "height": 512, "steps": steps, "cfg": 7, "seed": 20261103})
        jid = job.get("job_id")
        if not jid:
            print("   ✗ 没拿到 job_id：%s" % job)
            return 1
        while time.time() - t2 < 600:
            j = req("/api/job/" + jid, timeout=20)
            if j.get("state") == "done":
                files = j.get("files") or []
                print("   出图完成 %.1fs · %s" % (j.get("elapsed"), files))
                for f in files:
                    sz = png_size(f)
                    print("   %s · %d B · %s" % (os.path.basename(f), os.path.getsize(f),
                                                 ("%dx%d" % sz) if sz else "非 PNG?"))
                if files and png_size(files[0]) == (512, 512):
                    print("   ✓ 尺寸 512×512 对得上")
                return 0
            if j.get("state") == "error":
                print("   ✗ 出图失败：%s" % j.get("error"))
                return 1
            time.sleep(3)
        print("   ✗ 600s 内没出图")
        return 1
    finally:
        print("④ 收尸：停内核（连它拉起的 SD 后端一起）")
        kids = []
        try:
            import psutil
            kids = [c.pid for c in psutil.Process(r.proc.pid).children(recursive=True)] if r.proc else []
        except Exception:
            pass
        clean, alive = r.stop()
        deadline = time.time() + 20
        while time.time() < deadline and not r.port_free():
            time.sleep(0.5)
        print("   前端端口 %d 已释放：%s" % (PORT, r.port_free()))
        print("   pipe 判定：%s · 残留 pid %s" % ("干净" if clean else "不干净", alive or "无"))
        time.sleep(2)
        print("   stop 前抓到的后代 %s 里还活着的：%s" % (kids, _alive_pids(kids) or "无"))
        left = _orphans()
        print("   残留进程：%s" % (left or "无"))


def _alive_pids(pids):
    try:
        import psutil
    except ImportError:
        return []
    return [p for p in pids if psutil.pid_exists(p)]


def _orphans():
    try:
        import psutil
    except ImportError:
        return "(无 psutil，跳过)"
    hits = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cl = " ".join(p.info.get("cmdline") or [])
        except Exception:
            continue
        low = cl.replace("/", "\\").lower()
        if "neoforge-sd15" in low and ("kernel_main.py" in low or "webui.py" in low):
            hits.append("%s:%s" % (p.info["pid"], p.info.get("name")))
    return hits


if __name__ == "__main__":
    sys.exit(main())
