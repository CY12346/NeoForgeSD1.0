# -*- coding: utf-8 -*-
"""轨迹驱动回归：extract 抽事件 → apply 出片 → 连贯性体检。

默认拿本机留痕素材跑（可用环境变量换掉）：
    NEOFORGE_TRAJ_SRC    帧目录或视频（默认 D:/aicg/_traj_test/fish，51 帧 320x426 @10fps）
    NEOFORGE_TRAJ_SPRITE 立绘（默认 D:/aicg/_traj_test/assets_penguin/penguin_front.png）
    NEOFORGE_TRAJ_BG     背景（默认 D:/aicg/_traj_test/assets_penguin/bg.png）
素材不在就跳过（exit 0），不判失败。

断言：事件数 8–14、左右侧都出现、成片 COHERENT、帧数 == round(dur*fps)。
Run: python tests/traj_check.py
"""
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from core import quality, traj  # noqa: E402

SRC = os.environ.get("NEOFORGE_TRAJ_SRC", r"D:/aicg/_traj_test/fish")
SPRITE = os.environ.get("NEOFORGE_TRAJ_SPRITE", r"D:/aicg/_traj_test/assets_penguin/penguin_front.png")
BG = os.environ.get("NEOFORGE_TRAJ_BG", r"D:/aicg/_traj_test/assets_penguin/bg.png")
OUT = os.environ.get("NEOFORGE_TEST_OUT") or os.path.join(REPO, "outputs", "traj_check")
os.makedirs(OUT, exist_ok=True)

res = {"pass": True}


def fail(why):
    res.setdefault("failed_on", []).append(why)
    res["pass"] = False


missing = [p for p in (SRC, SPRITE) if not os.path.exists(p)]
if missing:
    print(json.dumps({"skipped": "missing assets", "missing": missing}, ensure_ascii=False))
    sys.exit(0)

# ------------------------------------------------------------------ extract
tj = traj.extract(SRC, os.path.join(OUT, "traj.json"), fps=10.0, mode="skin")
s = tj["summary"]
ev = tj["events"]
res["extract"] = {"n": s["n"], "size": s["size"], "detect_rate": s["detect_rate"],
                  "mean_area_pct": s["mean_area_pct"], "side_counts": s["side_counts"],
                  "events": len(ev), "times": [e["t"] for e in ev]}
if not (8 <= len(ev) <= 14):
    fail(f"events={len(ev)} 不在 8-14")
if not (s["side_counts"]["L"] and s["side_counts"]["R"]):
    fail("只检测到单侧")
if s["detect_rate"] < 90:
    fail(f"detect_rate={s['detect_rate']}")
if os.path.exists(SPRITE):
    res["extract"]["json_bytes"] = os.path.getsize(os.path.join(OUT, "traj.json"))

# ------------------------------------------------------------------ apply
r = traj.apply(tj, SPRITE, os.path.join(OUT, "traj_clip.mp4"),
               size=(640, 640), fps=24, loops=1, bg=BG if os.path.exists(BG) else None,
               sheet=os.path.join(OUT, "traj_clip_sheet.png"))
q = r["coherence"]
res["apply"] = {k: q[k] for k in ("frames", "size", "dur_s", "diff_subject", "diff_cv",
                                  "duplicate_frames", "flicker_pct", "verdict")}
res["apply"]["events"] = r["events"]
res["apply"]["video"] = r["path"]
exp = int(round(r["dur"] * 24))
if q["frames"] != exp:
    fail(f"frames={q['frames']} 期望 {exp}")
if q["verdict"] != "COHERENT":
    fail("verdict=" + str(q["verdict"]) + " failed_on=" + ",".join(q["failed_on"]))
if q["diff_subject"] < 1.0:
    fail(f"diff_subject={q['diff_subject']} 太小")

print(json.dumps(res, ensure_ascii=False, indent=2))
print("\nRESULT:", "PASS" if res["pass"] else "FAIL")
sys.exit(0 if res["pass"] else 1)
