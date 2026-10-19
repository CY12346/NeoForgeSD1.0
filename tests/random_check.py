# -*- coding: utf-8 -*-
"""随机测试：每次跑的输入都不一样，专门抓「样本里没写死」的坑。

R1  随机抽 N 个中文词条 → zh2en（本地词库，不联网）→ 必须输出英文、不含汉字
R2  随机拼 M 句（3–5 个词）→ 走 HTTP /api/translate → 结果非空、无汉字残留
R3  随机参数真出图（SD 在线才跑）：随机尺寸/步数/提示词 → job done + PNG 头宽高对得上
Run:  python tests/random_check.py [--round N] [--n 30] [--no-gpu]
"""
import argparse
import json
import os
import random
import struct
import sys
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
BASE = os.environ.get("NEOFORGE_TEST_BASE", "http://127.0.0.1:8899")
OUTDIR = os.environ.get("NEOFORGE_TEST_OUT", os.path.join(REPO, "_smoke"))

ap = argparse.ArgumentParser()
ap.add_argument("--round", type=int, default=1)
ap.add_argument("--n", type=int, default=30)
ap.add_argument("--sentences", type=int, default=5)
ap.add_argument("--no-gpu", action="store_true")
args = ap.parse_args()

random.seed(int(time.time() * 1000) % 100000 + args.round)   # 每轮都不同
res = {"round": args.round, "seed": random.random(), "steps": {}, "pass": True}


def fail(step, why):
    res["steps"].setdefault(step, {})["error"] = why
    res["pass"] = False


def get(path, timeout=30):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def post(path, payload, timeout=120):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(33)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", head[16:24])


CJK = lambda s: any("\u4e00" <= c <= "\u9fff" for c in s or "")

# ---------------------------------------------------------------- R1 单词随机
try:
    from core import i18n_tags as T
    d = T.load()
    pool = [zh for zh, hot in (d["hot"] or {}).items()
            if 2 <= len(zh) <= 5 and hot >= 1000 and zh not in T.COMMON
            and not any(c in zh for c in "（）()")]
    picks = random.sample(pool, min(args.n, len(pool)))
    ok, bad = 0, []
    t0 = time.time()
    for zh in picks:
        r = T.zh2en(zh, web=False)
        out = r.get("out") or ""
        if out and not CJK(out):
            ok += 1
        else:
            bad.append([zh, out])
    res["steps"]["R1_words"] = {"total": len(picks), "ok": ok, "bad": bad[:8],
                                "rate": round(ok / max(1, len(picks)), 3),
                                "elapsed": round(time.time() - t0, 2)}
    if ok / max(1, len(picks)) < 0.95:
        fail("R1_words", "命中率不足 95%")
except Exception as e:
    fail("R1_words", repr(e))

# ---------------------------------------------------------------- R2 随机句
try:
    sent_pool = random.sample(pool, min(12, len(pool)))
    sents, outs = [], []
    for _ in range(args.sentences):
        k = random.randint(3, 5)
        sents.append("，".join(random.sample(sent_pool, k)))
    bad_s = []
    t0 = time.time()
    for s in sents:
        r = post("/api/translate", {"text": s, "dir": "zh2en", "web": False}, timeout=60)
        out = r.get("out") or ""
        outs.append({"zh": s, "en": out, "miss": r.get("unknown")})
        if not out or CJK(out) or not any(c.isascii() and c.isalpha() for c in out):
            bad_s.append(s)
    res["steps"]["R2_sentences"] = {"n": len(sents), "bad": bad_s, "samples": outs,
                                    "elapsed": round(time.time() - t0, 2)}
    if bad_s:
        fail("R2_sentences", "有句子没翻干净: %s" % bad_s[:3])
except Exception as e:
    fail("R2_sentences", repr(e))

# ---------------------------------------------------------------- R3 随机出图
try:
    if args.no_gpu:
        res["steps"]["R3_gpu"] = {"skipped": "--no-gpu"}
    else:
        st = get("/api/status", timeout=20)
        if not st.get("online"):
            res["steps"]["R3_gpu"] = {"skipped": "SD 离线"}
        else:
            w, h = random.choice([(512, 512), (512, 768), (768, 512)])
            steps = random.choice([8, 10, 12, 16])
            words = [w_ for w_ in (T.zh2en(random.choice(pool), web=False)["out"] or "").split(", ") if w_]
            prompt = "masterpiece, best quality, " + ", ".join(words[:4])
            payload = {"prompt": prompt, "negative": "lowres, worst quality, blurry",
                       "width": w, "height": h, "steps": steps, "cfg": 7, "count": 1,
                       "sampler": random.choice(["DPM++ 2M", "Euler a", "DPM++ SDE"]),
                       "scheduler": "karras", "seed": -1}
            t0 = time.time()
            j = post("/api/image", payload, timeout=60)
            jid = j.get("job_id")
            final = None
            for _ in range(240):
                time.sleep(1)
                jj = get("/api/job/" + jid, timeout=20)
                if jj.get("state") in ("done", "error"):
                    final = jj
                    break
            el = round(time.time() - t0, 1)
            info = {"job": jid, "state": (final or {}).get("state"), "elapsed": el,
                    "want": [w, h], "steps": steps, "prompt": prompt,
                    "error": (final or {}).get("error")}
            sizes = []
            for url in (final or {}).get("image_urls") or []:
                p = os.path.join(os.environ.get("NEOFORGE_IMAGE_WORK", r"D:/aicg/_neoforge_console"),
                                 os.path.basename(url))
                sz = png_size(p) if os.path.isfile(p) else None
                sizes.append(sz)
            info["sizes"] = sizes
            res["steps"]["R3_gpu"] = info
            if info["state"] != "done" or not sizes or sizes[0] != (w, h):
                fail("R3_gpu", "出图失败或尺寸不符: %s" % info)
except Exception as e:
    fail("R3_gpu", repr(e))

os.makedirs(OUTDIR, exist_ok=True)
path = os.path.join(OUTDIR, "random_%d.json" % args.round)
with open(path, "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=2)
print(json.dumps(res, ensure_ascii=False, indent=2))
print("\nRESULT:", "PASS" if res["pass"] else "FAIL")
sys.exit(0 if res["pass"] else 1)
