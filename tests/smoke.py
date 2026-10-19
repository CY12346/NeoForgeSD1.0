# -*- coding: utf-8 -*-
"""NeoForgeSD 生图线端到端冒烟（不需要 SD 后端、不需要显卡、不需要额外依赖）

S1  byte-compile 全仓 python + node --check bridge
S2  起 ui/image_app.py（把 SD_API 指向关闭端口，保证「无后端」路径确定），真 HTTP 走一遍：
      /api/status（离线但目录/字段完整）、首页三区块、/api/history、
      /api/image | /api/img2img | /api/upscale | /api/model 无后端必须 503、未知 job 必须 404
证据写 $NEOFORGE_TEST_OUT/smoke.json（默认临时目录）
Run:  python tests/smoke.py
"""
import json
import os
import py_compile
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.environ.get("NEOFORGE_TEST_OUT") or tempfile.mkdtemp(prefix="nf_report_")
PORT = int(os.environ.get("NEOFORGE_TEST_PORT", "8897"))
BASE = "http://127.0.0.1:%d" % PORT
os.makedirs(OUT, exist_ok=True)
res = {"steps": {}, "pass": True}


def note(step, data):
    res["steps"].setdefault(step, {}).update(data)


def fail(step, why):
    res["steps"].setdefault(step, {})["error"] = why
    res["pass"] = False


# ------------------------------------------------------- S1 compile / syntax
try:
    files = []
    for root, dirs, fs in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "_parked")]
        files += [os.path.join(root, f) for f in fs if f.endswith(".py")]
    bad = []
    for f in files:
        try:
            py_compile.compile(f, doraise=True, cfile=os.path.join(tempfile.gettempdir(), "nf.pyc"))
        except Exception as e:
            bad.append((os.path.relpath(f, REPO), str(e)[:200]))
    note("S1_compile", {"files": len(files), "failed": bad})
    if bad:
        fail("S1_compile", "syntax errors")
    node = shutil.which("node")
    if node:
        r = subprocess.run([node, "--check", "bridge/server.mjs"], cwd=REPO,
                           capture_output=True, text=True)
        note("S1_node", {"returncode": r.returncode, "stderr": r.stderr.strip()[:300]})
        if r.returncode != 0:
            fail("S1_node", "node --check failed")
    else:
        note("S1_node", {"skipped": "node not on PATH"})
except Exception as e:
    fail("S1_compile", repr(e))

# ------------------------------------------------------- S2 live image app
work = tempfile.mkdtemp(prefix="nf_smoke_")
env = os.environ.copy()
env.update({
    "NEOFORGE_IMAGE_PORT": str(PORT), "NEOFORGE_IMAGE_WORK": work, "PYTHONUTF8": "1",
    # 冒烟必须「无后端」才有确定性：指向关闭端口
    "NEOFORGE_SD_API": os.environ.get("NEOFORGE_TEST_SD_API", "http://127.0.0.1:9"),
})
proc = subprocess.Popen([sys.executable, "ui/image_app.py"], cwd=REPO, env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def get(path, raw=False, timeout=30):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        body = r.read()
        return r.status, dict(r.headers), body if raw else json.loads(body.decode("utf-8"))


def post(path, payload, timeout=60):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


try:
    up = False
    for _ in range(80):
        try:
            socket.create_connection(("127.0.0.1", PORT), timeout=1).close()
            up = True
            break
        except OSError:
            time.sleep(0.25)
    note("S2_boot", {"port": PORT, "up": up})
    if not up:
        raise RuntimeError("image_app did not listen")

    st_code, _, st = get("/api/status")
    note("S2_status", {"code": st_code, "online": st.get("online"),
                       "backend": (st.get("backend") or {}).get("state"),
                       "work_ok": st.get("work") == work, "api": st.get("api")})
    if st_code != 200 or st.get("online") is not False:
        fail("S2_status", "expected offline status payload")
    if (st.get("backend") or {}).get("state") != "idle":
        fail("S2_status", "backend should be idle without SD")

    h_code, _, h_body = get("/", raw=True)
    html = h_body.decode("utf-8", "replace")
    marks = {
        "groups": all(x in html for x in ("① 创作", "② 后期", "③ 模型", "④ 资产")),
        "txt2img": "文生图" in html,
        "img2img": "图生图" in html and "/api/img2img" in html,
        "inpaint": "局部重绘" in html and "mask_b64" in html,
        "upscale": "/api/upscale" in html,
        "lora_hires": "LoRA" in html and "高清修复" in html,
        "adetailer": "面部修复" in html,
        "model_switch": "/api/model" in html,
        "vae": "/api/vae" in html,
        "pnginfo": "/api/pnginfo" in html,
        # 提示词助手（1.0.1）：中⇄英 / 词库 / 补全 / 风格
        "prompt_helper": all(x in html for x in ("提示词助手", "/api/translate",
                                                 "/api/promptlib", "/api/suggest")),
    }
    note("S2_page", {"code": h_code, "bytes": len(h_body), "marks": marks})
    if h_code != 200 or not all(marks.values()):
        fail("S2_page", "page missing expected blocks: %s" % [k for k, v in marks.items() if not v])

    hh_code, _, hh = get("/api/history?limit=10")
    note("S2_history", {"code": hh_code, "items": len(hh.get("items") or [])})
    if hh_code != 200 or not isinstance(hh.get("items"), list):
        fail("S2_history", "history endpoint unusable")

    for path, payload, label in (
        ("/api/image", {"prompt": "x"}, "image"),
        ("/api/img2img", {"prompt": "x", "image_b64": ""}, "img2img"),
        ("/api/upscale", {"image_b64": ""}, "upscale"),
        ("/api/model", {"name": "whatever"}, "model"),
        ("/api/vae", {"name": "whatever"}, "vae"),
        ("/api/pnginfo", {"image_b64": "x"}, "pnginfo"),
    ):
        code, j = post(path, payload)
        note("S2_guard_" + label, {"code": code, "error": (j.get("error") or "")[:120]})
        if code != 503:
            fail("S2_guard_" + label, "expected 503 without SD backend, got %s" % code)

    # S3 提示词助手（词典来自本机 SD 扩展；没有 SD 后端也能用）
    s3 = {}
    try:
        c, _, st3 = get("/api/i18n", timeout=60)
        s3["i18n"] = {"code": c, "zh": st3.get("zh_tags"), "en": st3.get("en_tags"),
                      "csv": st3.get("zh_csv"), "error": st3.get("error")}
        if c != 200 or (st3.get("zh_tags") or 0) < 50000:
            fail("S3_i18n", "词典没加载起来")
        c, _, sg = get("/api/suggest?q=long_h&limit=5", timeout=30)
        tags = [i.get("tag") for i in sg.get("items") or []]
        s3["suggest"] = {"code": c, "tags": tags}
        if "long_hair" not in tags:
            fail("S3_suggest", "补全没命中 long_hair: %s" % tags)
        c, tr = post("/api/translate", {"text": "长发，微笑的少女，俯视，森林", "dir": "zh2en"}, timeout=60)
        s3["zh2en"] = {"code": c, "out": tr.get("out"), "elapsed": tr.get("elapsed")}
        out = tr.get("out") or ""
        if not all(x in out for x in ("long hair", "smile", "from above", "forest")):
            fail("S3_zh2en", "中→英结果不对: %s" % out)
        c, tr2 = post("/api/translate", {"text": "1girl, long_hair, smile", "dir": "en2zh"}, timeout=30)
        s3["en2zh"] = {"code": c, "out": tr2.get("out")}
        if "长发" not in (tr2.get("out") or ""):
            fail("S3_en2zh", "英→中结果不对: %s" % tr2.get("out"))
        c, _, lib = get("/api/promptlib", timeout=60)
        n_tags = sum(len(g.get("tags") or []) for cat in lib.get("cats") or [] for g in cat.get("groups") or [])
        s3["promptlib"] = {"code": c, "cats": len(lib.get("cats") or []), "tags": n_tags}
        if not lib.get("cats") or n_tags < 1000:
            fail("S3_promptlib", "词库为空或过小: %s" % n_tags)
        c, _, sty = get("/api/styles", timeout=30)
        s3["styles_offline"] = {"code": c, "items": len(sty.get("items") or []), "online": sty.get("online")}
        if c != 200 or (sty.get("items") or []):
            fail("S3_styles", "无后端时 /api/styles 应返回空 items")
    except Exception as e:
        s3["fatal"] = "%s: %s" % (type(e).__name__, str(e)[:300])
        fail("S3_prompt_helper", s3["fatal"])
    note("S3_prompt_helper", s3)

    try:
        get("/api/job/deadbeef")
        note("S2_404", {"code": 200, "note": "unexpected"})
        fail("S2_404", "unknown job returned 200")
    except urllib.error.HTTPError as e:
        note("S2_404", {"code": e.code})
        if e.code != 404:
            fail("S2_404", "expected 404")
except Exception as e:
    fail("S2_live", repr(e))
finally:
    proc.terminate()
    try:
        tail = proc.communicate(timeout=10)[0].decode("utf-8", "replace")
    except Exception:
        proc.kill()
        tail = ""
    note("S2_app_log", tail.strip().splitlines()[:12])
    shutil.rmtree(work, ignore_errors=True)

with open(os.path.join(OUT, "smoke.json"), "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=2)

print(json.dumps(res, ensure_ascii=False, indent=2))
print("\nRESULT:", "PASS" if res["pass"] else "FAIL")
