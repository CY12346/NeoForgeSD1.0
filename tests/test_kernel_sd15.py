# -*- coding: utf-8 -*-
"""neoforge-sd15 内核端到端：壳那套管道起它、判活、停干净；缺环境要报人话。

    NEOFORGE_SILENT=1 python tests/test_kernel_sd15.py
"""
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.dirname(APP)                     # E:/NeoForge
sys.path.insert(0, APP)

from core import kernel, pipe                   # noqa: E402

KDIR = os.path.join(APP, "kernels", "neoforge-sd15")
MJ = os.path.join(KDIR, "kernel.json")
FAILED = []


def check(name, ok, detail=""):
    print(("  ✓ " if ok else "  ✗ ") + name + ((" · " + str(detail)) if detail else ""))
    if not ok:
        FAILED.append(name)


def port_free(port):
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) != 0


def main():
    print("=== ① 清单能读、占位符能展开 ===")
    m = kernel.load_manifest(MJ, base_dir=BASE)
    check("id = 目录名", m["id"] == os.path.basename(KDIR), m["id"])
    check("{python} 指向后端自带 python",
          m["command"][0].replace("\\", "/").endswith("backend/python/python.exe"), m["command"][0])
    check("{kernel} 展开成内核目录",
          os.path.isfile(os.path.join(m["cwd"], "kernel_main.py")), m["cwd"])
    check("{base}/backend 存在", os.path.isdir(os.path.join(BASE, "backend")))
    check("命令里没有残留占位符", "{" not in " ".join(m["command"]), " ".join(m["command"]))
    check("版本与包内一致",
          m["version"] == json.load(open(os.path.join(KDIR, "manifest.json"), encoding="utf-8"))["version"],
          m["version"])

    # 内核里三件复制品是构建产物：源改了忘了重建，这里必须炸（否则两份代码悄悄岔开）
    import hashlib
    drift = []
    for src, rel in (("ui/image_page.html", "neoforge/ui/image_page.html"),
                     ("core/winproc.py", "neoforge/_vendor/winproc.py"),
                     ("core/i18n_tags.py", "neoforge/_vendor/i18n_tags.py")):
        a = hashlib.sha256(open(os.path.join(APP, src), "rb").read()).hexdigest()
        b = hashlib.sha256(open(os.path.join(KDIR, rel), "rb").read()).hexdigest()
        if a != b:
            drift.append(rel)
    check("复制品与源一致（没忘了 rebuild）", not drift, drift or "3 个文件 sha256 相同")

    print("=== ①b 分层：每层都在，且下层不 import 上层 ===")
    pkg = os.path.join(KDIR, "neoforge")
    layers = ["config", "sdapi", "engine", "payload", "jobs", "history", "textfmt", "server"]
    missing = [m for m in layers if not os.path.isfile(os.path.join(pkg, m + ".py"))]
    check("8 层文件齐全", not missing, missing or ", ".join(layers))
    for lower, forbidden in (("config", ("sdapi", "engine", "payload", "jobs", "history",
                                         "textfmt", "server")),
                             ("sdapi", ("engine", "payload", "jobs", "history", "textfmt", "server"))):
        body = open(os.path.join(pkg, lower + ".py"), encoding="utf-8").read()
        bad = [f for f in forbidden
               if re.search(r"from\s+\.%s\s+import" % f, body)
               or (re.search(r"from\s+\.\s+import\s+[^\n]*\b%s\b" % f, body))
               or re.search(r"import\s+neoforge\.%s" % f, body)]
        check("%s.py 不反过来依赖上层" % lower, not bad, bad)
    km = open(os.path.join(KDIR, "kernel_main.py"), encoding="utf-8").read()
    imported = set()
    for chunk in re.findall(r"from\s+neoforge\s+import\s+([^\n#]+)", km):
        imported |= {x.strip().split()[0] for x in chunk.split(",") if x.strip()}
    imported |= set(re.findall(r"import\s+neoforge\.(\w+)", km))
    check("kernel_main 只 import server/config（不摸其它层）",
          imported <= {"server", "config"}, sorted(imported))

    print("=== ② 借壳的管道起它（start → PIPE:READY → /api/status）===")
    if not port_free(m["port"]):
        print("  ! 端口 %s 已被占用，先关掉正在跑的启动器/内核" % m["port"])
        return 1
    states, logs, prog = [], [], []
    r = pipe.KernelRunner(m, log=logs.append,
                          on_progress=lambda f, t: prog.append((f, t)),
                          on_state=lambda s, d="": states.append((s, d)))
    t0 = time.time()
    ok = r.start(wait_ready=90)
    took = time.time() - t0
    check("内核起来了", ok, r.last_error or "%.1fs" % took)
    if ok:
        check("状态到绿", any(s == "green" for s, _ in states), states[-1] if states else "")
        check("健康检查通", r.health_once())
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/status" % m["port"], timeout=5) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            check("/api/status 返回真状态", "online" in body and "sd_home" in body,
                  "online=%s" % body.get("online"))
            check("SD_HOME 是壳给的那份", body.get("sd_home", "").lower() ==
                  os.path.join(BASE, "backend").lower(), body.get("sd_home"))
        except Exception as e:
            check("/api/status 可读", False, e)
        check("stdout 打了 PIPE:READY", any("PIPE:READY" in x for x in logs),
              [x for x in logs if "PIPE:READY" in x][:1])
        check("stdout 打了 PIPE:PROGRESS", bool(prog) and any("[进度" in x for x in logs),
              prog[:3])

        print("=== ②b 接口面：拆完还要一一对得上（离线，不碰后端）===")
        base = "http://127.0.0.1:%d" % m["port"]

        def grab(path, data=None, timeout=8):
            r = urllib.request.Request(base + path,
                                       data=(json.dumps(data).encode() if data is not None else None),
                                       headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(r, timeout=timeout) as resp:
                    return resp.status, resp.read()
            except urllib.error.HTTPError as e:
                return e.code, e.read()

        code, page = grab("/")
        check("GET / 出页面", code == 200 and b"<html" in page.lower(), "%d / %d B" % (code, len(page)))
        for path in ("/api/catalog", "/api/history", "/api/styles", "/api/progress",
                     "/api/i18n"):
            code, body = grab(path)
            check("GET %s" % path, code in (200, 503),
                  "%d / %s" % (code, body[:60].decode("utf-8", "replace")))
        code, body = grab("/api/job/deadbeef0000")
        check("GET 不存在的 job → 404", code == 404, code)
        code, _ = grab("/files/nope.png")
        check("GET 不存在的文件 → 404", code == 404, code)
        code, body = grab("/api/image", {"prompt": "x"})
        check("POST /api/image 后端没起 → 503 说人话", code == 503 and "SD 后端未启动" in
              body.decode("utf-8", "replace"), code)
        code, body = grab("/api/translate", {"text": "", "dir": "zh2en"})
        check("POST /api/translate 可用或有明确原因", code in (200, 503), code)

        print("=== ③ 收尸：停完端口要放、不留孤儿 ===")
        r.stop()
        deadline = time.time() + 15
        while time.time() < deadline and not port_free(m["port"]):
            time.sleep(0.5)
        check("端口已释放", port_free(m["port"]))
        # 进程退出与 psutil 枚举之间有竞争：给它最多 6 s 落到 0（不是放宽标准，是别让时序误判）
        left = _orphans()
        deadline = time.time() + 6
        while left and time.time() < deadline:
            time.sleep(0.5)
            left = _orphans()
        check("没有残留的内核进程", not left, left)

    print("=== ④ 缺后端环境要报人话（退出码 3，不静默兜底）===")
    env = dict(os.environ, NEOFORGE_SD_HOME=os.path.join(APP, "_no_such_backend"),
               NEOFORGE_IMAGE_PORT="18901")
    out = subprocess.run([sys.executable, os.path.join(KDIR, "kernel_main.py")],
                         cwd=KDIR, env=env, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=60)
    check("退出码 3", out.returncode == 3, out.returncode)
    check("说了缺什么（人话）", "找不到 SD 后端目录" in (out.stdout + out.stderr),
          (out.stdout + out.stderr).strip().splitlines()[-1:] or "")

    print()
    if FAILED:
        print("KERNEL-SD15 FAIL：" + " / ".join(FAILED))
        return 1
    print("KERNEL-SD15 OK · 清单/起停/判活/收尸/缺环境 全过")
    return 0


def _orphans():
    """内核进程特征：命令行里带 kernels\\neoforge-sd15 的 python。"""
    try:
        import psutil
    except ImportError:
        return "（没 psutil，跳过进程级检查）"
    hits = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cl = " ".join(p.info.get("cmdline") or [])
        except Exception:
            continue
        if "kernel_main.py" in cl and "neoforge-sd15" in cl.replace("/", "\\"):
            hits.append("%s:%s" % (p.info["pid"], p.info.get("name")))
    return hits


if __name__ == "__main__":
    sys.exit(main())
