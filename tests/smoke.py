# -*- coding: utf-8 -*-
"""NeoForgeSD end-to-end smoke test (no SD backend needed).

S1  byte-compile every python module + node syntax check of bridge
S2  start ui/console.py on a scratch port, then over real HTTP:
      GET /api/status, GET /, POST /api/puppet -> poll -> media, 404 path,
      POST /api/motion without backend (expect 503)
Writes JSON evidence to $NEOFORGE_TEST_OUT/smoke.json (default: a temp dir).
Run:  python tests/smoke.py
"""
import base64
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
PORT = int(os.environ.get("NEOFORGE_TEST_PORT", "8898"))
BASE = "http://127.0.0.1:%d" % PORT

_CANDIDATES = [
    os.environ.get("NEOFORGE_TEST_SPRITE", ""),
    os.path.join(REPO, "tests", "sprite_hero.png"),
    r"D:/aicg/_motiontest/hero.png",
]
SPRITE = next((c for c in _CANDIDATES if c and os.path.isfile(c)), "")
os.makedirs(OUT, exist_ok=True)
res = {"steps": {}, "pass": True}


def note(step, data):
    res["steps"][step] = data


def fail(step, why):
    res["steps"].setdefault(step, {})["error"] = why
    res["pass"] = False


# ------------------------------------------------------- S1 compile / syntax
try:
    files = []
    for root, dirs, fs in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
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

# ------------------------------------------------------- S2 live console
work = tempfile.mkdtemp(prefix="nf_smoke_")
env = os.environ.copy()
env.update({"NEOFORGE_CONSOLE_PORT": str(PORT), "NEOFORGE_WORK": work, "PYTHONUTF8": "1"})
proc = subprocess.Popen([sys.executable, "ui/console.py"], cwd=REPO, env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def get(path, raw=False, timeout=30):
    req = urllib.request.Request(BASE + path)
    with urllib.request.urlopen(req, timeout=timeout) as r:
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
            s = socket.create_connection(("127.0.0.1", PORT), timeout=1)
            s.close()
            up = True
            break
        except OSError:
            time.sleep(0.25)
    note("S2_boot", {"port": PORT, "up": up})
    if not up:
        raise RuntimeError("console did not listen")

    st_code, _, st = get("/api/status")
    note("S2_status", {"code": st_code, "online": st.get("online"), "model": st.get("model"),
                       "animatediff": st.get("animatediff"), "actions": len(st.get("actions") or [])})
    if st_code != 200 or not st.get("actions"):
        fail("S2_status", "status payload unusable")

    h_code, h_hdr, h_body = get("/", raw=True)
    html = h_body.decode("utf-8", "replace")
    note("S2_page", {"code": h_code, "bytes": len(h_body),
                     "has_puppet_form": "/api/puppet" in html,
                     "title_ok": "NeoForgeSD" in html})
    if h_code != 200 or "/api/puppet" not in html:
        fail("S2_page", "page missing puppet form")

    code, j = post("/api/motion", {"image_b64": "", "prompt": "x"})
    note("S2_motion_guard", {"code": code, "error": j.get("error")})
    if code != 503:
        fail("S2_motion_guard", "expected 503 without SD backend")

    sprite = SPRITE
    if not sprite:  # 没有现成立绘就画一个：冒烟只验链路，不验画面
        from PIL import Image as _I, ImageDraw as _D
        sprite = os.path.join(OUT, "sprite_generated.png")
        im = _I.new("RGB", (400, 600), (255, 255, 255))
        d = _D.Draw(im)
        d.ellipse((120, 60, 280, 220), fill=(70, 110, 200))
        d.rounded_rectangle((140, 200, 260, 430), 40, fill=(230, 120, 60))
        d.ellipse((150, 430, 250, 520), fill=(60, 60, 70))
        im.save(sprite)
        note("S2_sprite", {"generated": sprite})
    code, j = post("/api/puppet", {"sprite_path": sprite, "action": "nod", "dur": 1.5,
                                   "fps": 24, "size": "480x480", "seed": 11})
    note("S2_puppet_submit", {"code": code, "job": j})
    jid = j.get("job_id")
    if not jid:
        raise RuntimeError("puppet job not accepted")

    t0 = time.time()
    job = {}
    while time.time() - t0 < 180:
        c, _, job = get("/api/job/" + jid)
        if job.get("state") in ("done", "error"):
            break
        time.sleep(0.5)
    note("S2_puppet_job", {"state": job.get("state"), "elapsed": job.get("elapsed"),
                           "media_url": job.get("media_url"),
                           "coherence": job.get("coherence")})
    if job.get("state") != "done":
        fail("S2_puppet_job", "job did not finish: %s" % job.get("error"))
    else:
        q = job.get("coherence") or {}
        if q.get("verdict") != "COHERENT":
            fail("S2_puppet_job", "coherence %s" % q.get("verdict"))
        m_code, m_hdr, m_body = get(job["media_url"], raw=True)
        note("S2_media", {"code": m_code, "bytes": len(m_body),
                          "content_type": m_hdr.get("Content-Type")})
        if m_code != 200 or len(m_body) < 20000 or b"ftyp" not in m_body[:64]:
            fail("S2_media", "media not a valid mp4")

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
    note("S2_console_log", tail.strip().splitlines()[:12])
    shutil.rmtree(work, ignore_errors=True)

with open(os.path.join(OUT, "smoke.json"), "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=2)

print(json.dumps(res, ensure_ascii=False, indent=2))
print("\nRESULT:", "PASS" if res["pass"] else "FAIL")
