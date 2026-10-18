#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""动作连贯性体检（不依赖 GPU）

一条视频算三组指标：
  1) 运动量   diff_mean        —— 相邻帧平均差（0-255）。<1 基本是静止图
  2) 流畅度   diff_cv / max_jerk —— 差分波动系数 / 突变尖峰。尖峰=跳帧/闪切
  3) 稳定度   duplicate / color_drift / flicker —— 重复帧、色彩漂移、整体闪烁

verdict: COHERENT / NEEDS_WORK
"""
import json
import os
import shutil
import subprocess
import tempfile

import numpy as np
from PIL import Image

_FF_CANDIDATES = [
    os.environ.get("FFMPEG", ""),
    r"E:/LosslessCut/resources/ffmpeg.exe",
    r"C:/ffmpeg/bin/ffmpeg.exe",
]
_PB_CANDIDATES = [
    os.environ.get("FFPROBE", ""),
    r"E:/LosslessCut/resources/ffprobe.exe",
    r"C:/ffmpeg/bin/ffprobe.exe",
]


def _tool(cands, name):
    for c in cands:
        if c and os.path.isfile(c):
            return c
    found = shutil.which(name)
    if found:
        return found
    raise FileNotFoundError(f"找不到 {name}，请把 FFMPEG/FFPROBE 环境变量指到可执行文件")


FFMPEG = _tool(_FF_CANDIDATES, "ffmpeg")
FFPROBE = _tool(_PB_CANDIDATES, "ffprobe")


def probe(path):
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,nb_frames,r_frame_rate,duration",
         "-of", "json", path], capture_output=True, text=True).stdout
    s = json.loads(out)["streams"][0]
    fps = s.get("r_frame_rate", "0/1")
    try:
        num, den = fps.split("/")
        s["fps"] = round(float(num) / float(den), 2)
    except Exception:
        s["fps"] = None
    s["n"] = int(s.get("nb_frames") or 0)
    return s


def decode(path, work=None):
    work = work or tempfile.mkdtemp(prefix="nfq_")
    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", path, os.path.join(work, "f%05d.png")],
                   check=True)
    fs = sorted(f for f in os.listdir(work) if f.endswith(".png"))
    imgs = [np.asarray(Image.open(os.path.join(work, f)).convert("RGB"), dtype=np.float32) for f in fs]
    return imgs, work


def coherence(path, frames=None, sheet=None):
    """返回指标字典 + verdict。frames 可传已解码的帧列表。"""
    info = probe(path)
    work = None
    if frames is None:
        frames, work = decode(path)
    n = len(frames)
    if n < 2:
        return {"video": path, "frames": n, "verdict": "NEEDS_WORK", "failed_on": ["too_short"]}

    d = np.array([float(np.abs(frames[i] - frames[i - 1]).mean()) for i in range(1, n)])
    dupe = [i + 1 for i, v in enumerate(d) if v < 0.12]
    jerk = np.abs(np.diff(d)) if len(d) > 1 else np.array([0.0])
    pos = d[d > 0.5]
    cv = float(pos.std() / pos.mean()) if len(pos) and pos.mean() > 0 else 0.0

    drift = np.array([float(np.abs(im - frames[0]).mean()) for im in frames])
    lum = np.array([float(im.mean()) for im in frames])
    flicker = float(lum.std() / lum.mean() * 100) if lum.mean() else 0.0

    # 主体位移：以首帧为参考的差分加权质心累计行程
    ref = frames[0].mean(axis=2)
    cx, cy = [], []
    for im in frames:
        w = np.clip(np.abs(im.mean(axis=2) - ref) - 8, 0, None)
        tot = w.sum()
        if tot < 1:
            cx.append(0.0)
            cy.append(0.0)
            continue
        ys, xs = np.nonzero(w > 0)
        cx.append(float((xs * w[ys, xs]).sum() / tot))
        cy.append(float((ys * w[ys, xs]).sum() / tot))
    travel = float(np.hypot(np.diff(cx), np.diff(cy)).sum()) if n > 1 else 0.0

    res = {
        "video": path,
        "frames": n,
        "size": f"{info.get('width')}x{info.get('height')}",
        "fps": info.get("fps"),
        "dur_s": round(n / info["fps"], 2) if info.get("fps") else None,
        "diff_mean": round(float(d.mean()), 2),
        "diff_cv": round(cv, 3),
        "max_jerk": round(float(jerk.max()), 2) if len(jerk) else 0.0,
        "duplicate_frames": dupe,
        "color_drift_end": round(float(drift[-1]), 2),
        "flicker_pct": round(flicker, 2),
        "subject_travel_px": round(travel, 1),
    }
    ok_motion = res["diff_mean"] >= 1.0 and res["subject_travel_px"] >= 8
    ok_smooth = res["diff_cv"] <= 0.85 and res["max_jerk"] <= 12.0
    ok_stable = len(dupe) == 0 and res["color_drift_end"] <= 45 and res["flicker_pct"] <= 6
    res["failed_on"] = [k for k, v in (("motion", ok_motion), ("smooth", ok_smooth),
                                       ("stable", ok_stable)) if not v]
    res["verdict"] = "COHERENT" if not res["failed_on"] else "NEEDS_WORK"

    if sheet:
        idx = [int(i * (n - 1) / 7) for i in range(8)] if n >= 8 else list(range(n))
        th = 256
        canvas = Image.new("RGB", (th * len(idx), th))
        for k, i in enumerate(idx):
            canvas.paste(Image.fromarray(frames[i].astype(np.uint8)).resize((th, th)), (k * th, 0))
        canvas.save(sheet)
        res["sheet"] = sheet

    if work:
        shutil.rmtree(work, ignore_errors=True)
    return res


if __name__ == "__main__":
    import sys
    p = sys.argv[1]
    sheet = os.path.splitext(p)[0] + "_sheet.png"
    print(json.dumps(coherence(p, sheet=sheet), ensure_ascii=False, indent=2))
