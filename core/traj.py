#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""轨迹驱动（`core/traj.py`）—— 把现成片子里的运动抽成事件，用它们驱动立绘木偶。

两段式，各管一件事：

    extract()  现成视频/GIF/帧序列 ──► traj.json
               每帧：主体质心 / 面积 / 主轴角 / 在画面左还是右 / 速度
               事件：面积局部峰（一次"出手"就是一个事件），带强度

    apply()    traj.json + 一张立绘 ──► 木偶成片
               事件通过冲量曲线转成形变参数（抬臂 / 倾斜 / 挤压 / 前倾），
               可选叠冲击线与递减抖动；出片后自动跑 quality.coherence

提取两种口径：
    motion（默认，通用）  帧差前景 vs 时间中位帧 → 最大团块。任何主体都能用（拳/走/跳/挥手）。
    skin（肤色 prior）    复刻“蓝色大肥鱼”那支的做法：肤色出现率 >40% 的像素算静态肤色（脸/手背），
                          只在少数帧才变肤色的团块才是挥动的手。

依赖：numpy + PIL（apply 全流程）；extract 只多要 ffmpeg（复用 core.quality.FFMPEG 的探测）。
命令行：
    python -m core.traj extract <视频|帧目录> --out traj.json [--fps 10] [--start 0] [--dur 5] [--mode motion|skin]
    python -m core.traj apply --sprite hero.png --traj traj.json --out out.mp4
"""
import argparse
import json
import math
import os
import subprocess
import tempfile

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import quality

# ------------------------------------------------------------------ 小工具


def _rgb(path):
    im = Image.open(path)
    if im.mode == "RGBA":                        # 视频帧常带 alpha，统一压到黑底
        bg = Image.new("RGB", im.size, (0, 0, 0))
        bg.paste(im, (0, 0), im)
        return np.asarray(bg, dtype=np.uint8)
    return np.asarray(im.convert("RGB"), dtype=np.uint8)


def load_frames(src, fps=10.0, start=0.0, dur=None):
    """视频 → 帧列表；也可以直接给一帧目录（png/jpg，按文件名排序）。"""
    if os.path.isdir(src):
        names = sorted(n for n in os.listdir(src) if n.lower().endswith((".png", ".jpg", ".jpeg")))
        if not names:
            raise SystemExit(f"{src} 里没有帧图")
        frames = [_rgb(os.path.join(src, n)) for n in names]
        # 目录里常混着调试图/接触表：只保留出现最多的那个尺寸（帧序列），其余当噪声丢掉
        sizes = {}
        for f in frames:
            sizes[f.shape] = sizes.get(f.shape, 0) + 1
        keep = max(sizes, key=sizes.get)
        if len(sizes) > 1:
            print(f"[traj] 混入 {len(frames) - sizes[keep]} 张非帧图（尺寸不一），已跳过")
        return [f for f in frames if f.shape == keep], float(fps)

    tmp = tempfile.mkdtemp(prefix="nftraj_")
    cmd = [quality.FFMPEG, "-v", "error", "-y"]
    if start:
        cmd += ["-ss", str(start)]
    if dur:
        cmd += ["-t", str(dur)]
    cmd += ["-i", src, "-vf", f"fps={fps}", os.path.join(tmp, "s_%05d.png")]
    subprocess.run(cmd, check=True)
    names = sorted(n for n in os.listdir(tmp) if n.endswith(".png"))
    if not names:
        raise SystemExit(f"抽不到帧：{src}")
    return [_rgb(os.path.join(tmp, n)) for n in names], float(fps)


def _hsv(rgb):
    """OpenCV 口径的 HSV（H 0-180, S/V 0-255），纯 numpy 实现，省掉 cv2 依赖。"""
    a = rgb.astype(np.float32) / 255.0
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    mx = a.max(axis=2)
    mn = a.min(axis=2)
    d = mx - mn
    safe = np.maximum(d, 1e-6)
    h = np.where(mx == r, ((g - b) / safe) % 6,
                 np.where(mx == g, (b - r) / safe + 2, (r - g) / safe + 4))
    h = h * 60.0                                  # 0-360 度
    s = np.where(mx > 1e-6, d / np.maximum(mx, 1e-6), 0.0)
    return h / 2.0, s * 255.0, mx * 255.0        # → OpenCV 口径


def skin_mask(rgb):
    h, s, v = _hsv(rgb)
    return ((h > 3) & (h < 34) & (s > 25) & (s < 155) & (v > 135))


def motion_mask(frames, thresh=18.0):
    """对时间中位帧做差分：任何"会动的东西"都会浮出来。"""
    stack = np.stack(frames, 0).astype(np.float32)
    med = np.median(stack, axis=0)
    return [np.abs(f.astype(np.float32) - med).mean(axis=2) > thresh for f in frames]


def _dilate(mask, k):
    """方形膨胀（k 为奇数），借 PIL 的 MaxFilter，省掉自己写卷积。"""
    if k <= 1:
        return mask
    m = Image.fromarray((mask.astype(np.uint8) * 255))
    return np.asarray(m.filter(ImageFilter.MaxFilter(k if k % 2 else k + 1))) > 127


def _open(mask, k):
    """先膨胀后腐蚀（开运算）：抹掉孤立噪点。"""
    if k <= 1:
        return mask
    k = k if k % 2 else k + 1
    m = Image.fromarray((mask.astype(np.uint8) * 255))
    m = m.filter(ImageFilter.MaxFilter(k)).filter(ImageFilter.MinFilter(k))
    return np.asarray(m) > 127


def _close(mask, k):
    """先腐蚀后膨胀（闭运算）：把碎成几块的手/拳粘回一个团。"""
    if k <= 1:
        return mask
    k = k if k % 2 else k + 1
    m = Image.fromarray((mask.astype(np.uint8) * 255))
    m = m.filter(ImageFilter.MinFilter(k)).filter(ImageFilter.MaxFilter(k))
    return np.asarray(m) > 127


def _blobs(mask, min_area=250):
    """连通域（BFS），返回 [(面积, cx, cy, bbox, 主轴角°)]，按面积降序。"""
    H, W = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    ys, xs = np.nonzero(mask)
    out = []
    for sy, sx in zip(ys, xs):
        if seen[sy, sx]:
            continue
        stack = [(sy, sx)]
        seen[sy, sx] = True
        px = []
        while stack:
            y, x = stack.pop()
            px.append((y, x))
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < H and 0 <= nx < W and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        if len(px) < min_area:
            continue
        arr = np.array(px, dtype=np.float32)     # (y, x)
        cy, cx = arr[:, 0].mean(), arr[:, 1].mean()
        cov = np.cov((arr - arr.mean(0)).T)
        ev, evec = np.linalg.eigh(cov)
        v = evec[:, int(np.argmax(ev))]
        ang = math.degrees(math.atan2(v[0], v[1]))
        y0, x0 = arr.min(0)
        y1, x1 = arr.max(0)
        out.append((len(px), float(cx), float(cy),
                    (int(x0), int(y0), int(x1 - x0 + 1), int(y1 - y0 + 1)), float(ang)))
    out.sort(reverse=True)
    return out


# ------------------------------------------------------------------ extract


def extract(src, out=None, fps=10.0, start=0.0, dur=None, mode="motion", min_area_pct=0.3):
    """抽轨迹 → dict（并（可选）落盘为 json）。"""
    frames, fps = load_frames(src, fps=fps, start=start, dur=dur)
    n = len(frames)
    H, W = frames[0].shape[:2]
    min_area = max(120, int(W * H * min_area_pct / 100.0))

    if mode == "skin":
        masks = [skin_mask(f) for f in frames]
        freq = np.mean(np.stack(masks, 0), axis=0)
        static = _dilate(freq > 0.40, 7)
        moved = [m & ~static for m in masks]     # 只在少数帧才肤色的 = 挥动的手
        moved = [_close(_open(m, 3), 9) for m in moved]
    elif mode == "motion":
        moved = [np.logical_and(m, np.ones_like(m)) for m in motion_mask(frames)]
        moved = [_dilate(m, 5) for m in moved]
    else:
        raise SystemExit(f"未知 mode：{mode}（可选 motion / skin）")

    recs = []
    for i, m in enumerate(moved):
        blobs = _blobs(m, min_area)
        rec = {"i": i, "t": round(i / fps, 3), "detected": bool(blobs)}
        if blobs:
            area, cx, cy, bbox, ang = blobs[0]
            rec.update({"area_pct": round(100.0 * area / (W * H), 2),
                        "centroid": [round(cx / W, 4), round(cy / H, 4)],
                        "bbox": list(bbox), "angle_deg": round(ang, 1),
                        "side": "R" if cx > W / 2 else "L"})
        else:
            rec.update({"area_pct": 0.0, "centroid": None, "bbox": None,
                        "angle_deg": None, "side": None})
        recs.append(rec)

    for i, r in enumerate(recs):
        prev = recs[i - 1]["centroid"] if i else None
        if r["centroid"] and prev:
            d = np.array(r["centroid"]) - np.array(prev)
            r["dxy"] = [round(float(d[0]), 4), round(float(d[1]), 4)]
            r["speed"] = round(float(np.linalg.norm(d)) * fps, 3)   # 画面宽/秒
        else:
            r["dxy"] = [0.0, 0.0]
            r["speed"] = 0.0

    # 事件 = 面积曲线的局部峰（一次出手/一次挥手）
    area = np.array([r["area_pct"] for r in recs])
    pos = area[area > 0]
    thresh = float(np.percentile(pos, 60)) if pos.size else 0.0
    peak = max(1e-6, float(area.max()) if area.size else 1.0)
    events = []
    for i in range(1, n - 1):
        if area[i] >= area[i - 1] and area[i] >= area[i + 1] and area[i] > thresh:
            events.append({"i": i, "t": recs[i]["t"], "area_pct": recs[i]["area_pct"],
                           "strength": round(float(area[i] / peak), 3),
                           "centroid": recs[i]["centroid"], "side": recs[i]["side"],
                           "angle_deg": recs[i]["angle_deg"]})

    det = [r for r in recs if r["detected"]]
    res = {
        "summary": {
            "src": os.path.abspath(src), "mode": mode, "n": n, "size": [W, H], "fps": round(fps, 3),
            "src_dur": round(n / fps, 3),
            "detect_rate": round(100.0 * len(det) / max(1, n), 1),
            "mean_area_pct": round(float(area.mean()), 2),
            "side_counts": {"L": sum(1 for r in det if r["side"] == "L"),
                            "R": sum(1 for r in det if r["side"] == "R")},
            "max_speed": round(max((r["speed"] for r in recs), default=0.0), 2),
            "events": len(events),
        },
        "frames": recs,
        "events": events,
    }
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    return res


# ------------------------------------------------------------------ apply


def _impulse(dt, rise=0.05, decay=0.15):
    """一次事件的冲量：rise 秒内冲到 1，然后指数回落。"""
    if dt < -0.08 or dt > 0.42:
        return 0.0
    if dt < rise:
        x = min(1.0, max(0.0, (dt + 0.08) / (rise + 0.08)))
        return float(1.0 - (1.0 - x) ** 2)          # ease-out，起手快、落手慢
    return float(math.exp(-(dt - rise) / decay))


def make_params(events, src_dur, pad=0.35, loops=1, gain=1.0):
    """事件 → 形变参数（签名与 motion.action_params 一致）。"""
    ev = [(e["t"], e.get("side") or "R", float(e.get("angle_deg") or 0.0),
           float(e.get("strength") or 1.0)) for e in events]
    span = src_dur * max(1, loops)

    def wrap(dt):
        return (dt + src_dur / 2) % src_dur - src_dur / 2

    def fn(name, t, dur, P):
        ts = (t - pad) % src_dur if t >= pad else 0.0
        arm_l = arm_r = tilt = lean = squash = 0.0
        for te, side, ang, st in ev:
            imp = _impulse(wrap(ts - te))
            if imp <= 0.0:
                continue
            amp = gain * (0.72 + 0.28 * st) * (0.90 + 0.16 * math.sin(math.radians(ang)))
            if side == "R":
                arm_r = max(arm_r, -amp * imp)
                arm_l = max(arm_l, -0.10 * imp)
                lean = min(lean, -0.026 * imp)
                tilt += -1.6 * imp
            else:
                arm_l = max(arm_l, -amp * imp)
                arm_r = max(arm_r, -0.10 * imp)
                lean = max(lean, 0.026 * imp)
                tilt += 1.6 * imp
            squash = max(squash, 0.018 * imp)
        # 待机微摆：保证两拍之间也不是死帧
        arm_l += 0.035 * math.sin(2 * math.pi * t / 4.1)
        arm_r += 0.035 * math.sin(2 * math.pi * t / 4.1 + 0.4)
        p = min(max(t / max(span + pad, 1e-6), 0.0), 1.0)
        return dict(arm_l=arm_l, arm_r=arm_r, tilt=tilt, squash=squash, hop=0.0,
                    lean=lean, p=p)

    return fn


def _draw_impact(img, fx, fy, side, strength, ang_deg, tint=(60, 140, 255)):
    """冲击线：从命中点向外放射，强度随时间衰减。"""
    W, H = img.size
    k = max(1, W // 360)
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    alpha = int(255 * 0.42 * strength)
    base = math.radians(ang_deg) * 0.25 + (math.pi if side == "L" else 0.0)
    for j in range(9):
        a0 = base + (j - 4) * 0.34
        r0 = 8 + 8 * (1 - strength)
        r1 = r0 + (20 + 26 * strength)
        d.line([(fx + r0 * math.cos(a0), fy + r0 * math.sin(a0)),
                (fx + r1 * math.cos(a0), fy + r1 * math.sin(a0))],
               fill=tint + (alpha,), width=3 * k)
    rr = 8 + 26 * (1 - strength)
    d.ellipse([fx - rr, fy - rr, fx + rr, fy + rr], outline=tint + (alpha,), width=3 * k)
    return Image.alpha_composite(img.convert("RGBA"), lay)


def _shake(img, dx, dy):
    W, H = img.size
    edge = img.convert("RGB").getpixel((0, 0))
    out = img.transform((W, H), Image.AFFINE, (1, 0, -dx, 0, 1, -dy),
                        resample=Image.BILINEAR, fillcolor=edge + (255,))
    return out


def apply(traj, sprite, out, size=None, fps=24, pad=0.35, loops=1, bg=None,
          height=0.72, gain=1.0, effects=True, sheet=None, metrics=True):
    """traj（dict 或 json 路径）+ 立绘 → 木偶成片。"""
    from . import motion

    if isinstance(traj, str):
        with open(traj, encoding="utf-8") as f:
            traj = json.load(f)
    events = traj.get("events") or []
    if not events:
        raise SystemExit("这条轨迹里没有事件：换 --mode 或放宽 --min-area")
    src_dur = float(traj["summary"]["src_dur"])
    W, H = size or (720, 720)
    dur = round(pad * 2 + src_dur * max(1, loops), 3)

    if isinstance(sprite, Image.Image):
        has_alpha = sprite.mode == "RGBA" and np.asarray(sprite.split()[-1]).min() < 8
    else:
        im = Image.open(sprite)
        has_alpha = im.mode == "RGBA" and np.asarray(im.split()[-1]).min() < 8

    motion.register_action("traj", make_params(events, src_dur, pad, loops, gain),
                           f"轨迹驱动：{len(events)} 个事件 / 源 {src_dur:.2f}s")
    pup = motion.Puppet(sprite, size=(W, H), bg=bg, height=height,
                        cutout=False if has_alpha else None)
    r = pup.render("traj", dur=dur, fps=fps, out=out)

    if effects:
        r["path"] = _impact_pass(r["path"], events, src_dur, pad, pup, fps)
    if sheet:
        r["sheet"] = sheet
    if metrics:
        r["coherence"] = quality.coherence(r["path"], sheet=sheet)
    r["events"] = len(events)
    return r


def _impact_pass(mp4, events, src_dur, pad, pup, fps):
    """第二遍：按同一批事件在命中侧加冲击线 + 递减抖动（PIL 版，不依赖 cv2）。"""
    tmp = tempfile.mkdtemp(prefix="nftfx_")
    subprocess.run([quality.FFMPEG, "-v", "error", "-y", "-i", mp4, os.path.join(tmp, "i_%04d.png")],
                   check=True)
    files = sorted(f for f in os.listdir(tmp) if f.startswith("i_"))
    if not files:
        return mp4
    W, H = pup.W, pup.H
    cw, ch = pup.char.width, pup.char.height
    for k, name in enumerate(files):
        t = k / fps
        ts = (t - pad) % src_dur if t >= pad else 0.0
        img = Image.open(os.path.join(tmp, name)).convert("RGB")
        shake = 0.0
        for e in events:
            dt = (ts - e["t"] + src_dur / 2) % src_dur - src_dur / 2
            if dt < -0.04 or dt > 0.34:
                continue
            strg = (1.0 - max(0.0, dt) / 0.34) ** 1.4 * float(e.get("strength") or 1.0)
            side = e.get("side") or "R"
            c = e.get("centroid") or [0.9 if side == "R" else 0.1, 0.6]
            fx = int((W - cw) // 2 + min(1.0, max(0.0, c[0])) * cw)
            fy = int(pup.ground + min(1.0, max(0.0, c[1])) * ch)
            img = _draw_impact(img, fx, fy, side, strg, float(e.get("angle_deg") or 0.0))
            img = img.convert("RGB")
            shake = max(shake, 6.0 * strg)
        if shake > 0.05:
            img = _shake(img, shake * math.sin(t * 57), shake * math.cos(t * 43)).convert("RGB")
        img.save(os.path.join(tmp, f"e_{k:05d}.png"))

    subprocess.run([quality.FFMPEG, "-v", "error", "-y", "-framerate", str(fps), "-i",
                    os.path.join(tmp, "e_%05d.png"), "-c:v", "libx264", "-crf", "17",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", mp4], check=True)
    return mp4


# ------------------------------------------------------------------ CLI


def main(argv=None):
    ap = argparse.ArgumentParser(description="NeoForgeSD 轨迹驱动（抽事件 / 驱动木偶）")
    sub = ap.add_subparsers(dest="cmd")

    pe = sub.add_parser("extract", help="视频/帧目录 → traj.json")
    pe.add_argument("src")
    pe.add_argument("--out", required=True)
    pe.add_argument("--fps", type=float, default=10.0)
    pe.add_argument("--start", type=float, default=0.0)
    pe.add_argument("--dur", type=float, default=None)
    pe.add_argument("--mode", default="motion", choices=["motion", "skin"])
    pe.add_argument("--min-area", type=float, default=0.3, help="最小团块面积（占画面 %%）")

    pa = sub.add_parser("apply", help="traj.json + 立绘 → 成片")
    pa.add_argument("--sprite", required=True)
    pa.add_argument("--traj", required=True)
    pa.add_argument("--out", required=True)
    pa.add_argument("--size", default="720x720")
    pa.add_argument("--fps", type=int, default=24)
    pa.add_argument("--pad", type=float, default=0.35)
    pa.add_argument("--loops", type=int, default=1)
    pa.add_argument("--bg", default=None)
    pa.add_argument("--height", type=float, default=0.72)
    pa.add_argument("--gain", type=float, default=1.0)
    pa.add_argument("--no-effects", action="store_true")
    pa.add_argument("--sheet", default=None)

    a = ap.parse_args(argv)
    if a.cmd == "extract":
        res = extract(a.src, a.out, fps=a.fps, start=a.start, dur=a.dur,
                      mode=a.mode, min_area_pct=a.min_area)
        print(json.dumps(res["summary"], ensure_ascii=False))
        print("events:", [(e["t"], e["side"], e["strength"]) for e in res["events"]])
        print("->", os.path.abspath(a.out))
        return
    if a.cmd == "apply":
        w, h = (int(v) for v in a.size.lower().split("x"))
        r = apply(a.traj, a.sprite, a.out, size=(w, h), fps=a.fps, pad=a.pad,
                  loops=a.loops, bg=a.bg, height=a.height, gain=a.gain,
                  effects=not a.no_effects, sheet=a.sheet)
        print(json.dumps({k: v for k, v in r.items() if k != "coherence"}, ensure_ascii=False))
        if r.get("coherence"):
            q = r["coherence"]
            print(json.dumps({k: q[k] for k in ("frames", "size", "diff_subject", "diff_cv",
                                                "duplicate_frames", "flicker_pct", "verdict")},
                             ensure_ascii=False))
        return
    ap.print_help()


if __name__ == "__main__":
    main()
