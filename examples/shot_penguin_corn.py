#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""示例分镜：小企鹅在玉米地里掰玉米 → 察觉身后有人 → 转身挥手。

用法（仓库根目录）：
    python examples/shot_penguin_corn.py --sheet "C:/Users/.../小企鹅.png" --out D:/aicg/成片/penguin.mp4

技术点：
  - 三视图（正面/侧面/背面）换图 + 横向挤压 = 转身，不用真 3D
  - 草帽/玉米是程序化绘制，跟着头部倾角一起转
  - 正机位挥手用 core/motion.py 的形变场（绕肩旋转），背/侧机位只用整体摆动
"""
import argparse
import math
import os
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from core.motion import _warp, ease, seg, cutout_white  # noqa: E402
from core import quality  # noqa: E402

CANVAS = 900          # 合成画布（比成片大，留给推镜）
OUT = 720             # 成片边长
FPS = 24
DUR = 7.0


# ------------------------------------------------------------------ 素材
def split_sheet(path):
    """三视图横向切分 + 抠图（近白底 / 浅灰投影）"""
    src = Image.open(path).convert("RGB")
    a = np.asarray(src).astype(np.int16)
    col_white = (a.min(axis=2) > 245).mean(axis=0) > 0.985
    runs, start = [], None
    for i, w in enumerate(col_white):
        if not w and start is None:
            start = i
        elif w and start is not None:
            if i - start > 60:
                runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(col_white)))
    if len(runs) < 3:
        raise SystemExit(f"没切出三视图，只找到 {len(runs)} 块: {runs}")
    views = []
    for x0, x1 in runs[:3]:
        im = src.crop((max(0, x0 - 10), 0, min(src.width, x1 + 10), src.height))
        views.append(clean_sprite(im))
    return views


def clean_sprite(im, strict=242, sat_tol=10, bottom_lo=205, with_bbox=False):
    a = np.asarray(im.convert("RGB")).astype(np.int16)
    mn = a.min(axis=2)
    sat = a.max(axis=2) - mn
    cand = np.logical_and(mn >= strict, sat <= sat_tol)

    h, w = cand.shape
    bg = np.zeros_like(cand)
    from collections import deque
    q = deque()
    for x in range(w):
        for y in (0, h - 1):
            if cand[y, x] and not bg[y, x]:
                bg[y, x] = True
                q.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if cand[y, x] and not bg[y, x]:
                bg[y, x] = True
                q.append((y, x))
    while q:
        y, x = q.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and cand[ny, nx] and not bg[ny, nx]:
                bg[ny, nx] = True
                q.append((ny, nx))

    y0 = int(h * 0.72)
    loose = np.logical_and(mn >= bottom_lo, sat <= 18)
    loose[:y0, :] = False
    bg = np.logical_or(bg, loose)

    alpha = np.where(bg, 0, 255).astype(np.uint8)
    al = Image.fromarray(alpha).filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.7))
    out = im.convert("RGBA")
    out.putalpha(al)
    bbox = out.getbbox() or (0, 0, out.width, out.height)
    if with_bbox:
        return out.crop(bbox), bbox
    return out.crop(bbox)


def make_straw_hat(width=430):
    """程序化草帽：帽檐（编织圈纹）+ 圆顶 + 帽带，形状用蒙版裁切"""
    S = 3
    W = width * S
    brim_h = int(W * 0.26)
    crown_w = int(W * 0.54)
    crown_h = int(W * 0.30)
    H = int(crown_h + brim_h * 0.70)

    straw = (226, 190, 122)
    straw_d = (176, 138, 74)
    straw_l = (243, 216, 158)
    band = (150, 108, 46)

    # 蒙版
    brim = Image.new("L", (W, H), 0)
    ImageDraw.Draw(brim).ellipse([0, H - brim_h, W, H - 1], fill=255)
    crown = Image.new("L", (W, H), 0)
    cx = W // 2
    ImageDraw.Draw(crown).ellipse([cx - crown_w // 2, 0, cx + crown_w // 2, crown_h], fill=255)

    # 帽檐底色 + 径向/同心编织纹（都裁在帽檐内）
    brim_img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(brim_img)
    bd.ellipse([0, H - brim_h, W, H - 1], fill=straw + (255,))
    for k in range(1, 5):
        ry = brim_h * 0.14 * k
        rx = W * 0.62 * (0.22 * k)
        bd.arc([cx - rx, H - brim_h / 2 - ry, cx + rx, H - brim_h / 2 + ry],
               195, 345, fill=straw_d + (150,), width=S * 2)
    for ang in range(-78, 79, 13):
        ca, sa = math.cos(math.radians(ang)), math.sin(math.radians(ang))
        bd.line([(cx, H - brim_h * 0.45),
                 (cx + ca * W * 0.50, H - brim_h * 0.45 + sa * brim_h * 0.42)],
                fill=(206, 168, 100, 200), width=S)
    brim_img.putalpha(Image.composite(brim, Image.new("L", (W, H), 0), brim))

    # 帽顶：左亮右暗的圆柱感 + 底部帽带
    crown_img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(crown_img)
    cd.ellipse([cx - crown_w // 2, 0, cx + crown_w // 2, crown_h], fill=straw + (255,))
    cd.ellipse([cx - crown_w // 2 + S * 4, S * 6, cx + crown_w // 2 - S * 10, crown_h - S * 6],
               fill=straw_l + (110,))
    for k in range(1, 6):     # 竖向编织
        xx = cx - crown_w // 2 + crown_w * k / 6.0
        cd.line([(xx, crown_h * 0.12), (xx, crown_h * 0.86)], fill=(206, 168, 100, 120), width=S)
    cd.rectangle([cx - crown_w // 2, int(crown_h * 0.66), cx + crown_w // 2, int(crown_h * 0.88)],
                 fill=band + (255,))
    crown_img.putalpha(Image.composite(crown, Image.new("L", (W, H), 0), crown))

    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out.alpha_composite(brim_img)
    out.alpha_composite(crown_img)
    # 帽檐在帽顶上的投影
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shade).ellipse([cx - crown_w // 2 - S * 6, crown_h * 0.80,
                                   cx + crown_w // 2 + S * 6, crown_h * 0.96],
                                  fill=(90, 62, 20, 55))
    out.alpha_composite(shade)
    return out.resize((W // S, H // S), Image.LANCZOS)


def make_corn_stalk(height=520):
    """程序化玉米秆：秆 + 叶片 + 玉米棒（苞叶包着，颗粒裁在棒体内）"""
    S = 3
    w = int(height * 0.42) * S
    h = height * S
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    g_d, g, g_l = (68, 108, 42), (104, 152, 60), (150, 190, 96)

    cx = w * 0.44
    d.line([(cx + w * 0.02, h), (cx - w * 0.01, h * 0.16)], fill=g + (255,), width=int(w * 0.05))
    for yy, sgn, L in [(0.86, 1, 0.46), (0.72, -1, 0.50), (0.58, 1, 0.42), (0.44, -1, 0.36)]:
        y = h * yy
        tipx = cx + sgn * w * L
        tipy = y - h * 0.11
        d.polygon([(cx, y + h * 0.02), (cx + sgn * w * 0.13, y - h * 0.04),
                   (tipx, tipy), (cx + sgn * w * 0.10, y + h * 0.05)], fill=g + (255,))
        d.line([(cx, y + h * 0.01), (tipx, tipy)], fill=g_d + (255,), width=S)

    # 玉米棒：棒体椭圆 → 颗粒裁在棒体内 → 苞叶包底
    cob_w, cob_h = w * 0.34, h * 0.30
    bx, by = cx + w * 0.20, h * 0.42
    body = Image.new("L", (w, h), 0)
    ImageDraw.Draw(body).ellipse([bx - cob_w / 2, by, bx + cob_w / 2, by + cob_h], fill=255)

    cob = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    cd = ImageDraw.Draw(cob)
    cd.ellipse([bx - cob_w / 2, by, bx + cob_w / 2, by + cob_h], fill=(232, 178, 34, 255))
    rows, cols = 10, 5
    for r in range(rows):
        yy = by + cob_h * 0.10 + (cob_h * 0.80) * r / (rows - 1)
        for c in range(cols):
            xx = bx - cob_w * 0.36 + cob_w * 0.72 * (c + (0.5 if r % 2 else 0.0)) / (cols - 1)
            rr = cob_w * 0.085
            cd.ellipse([xx - rr, yy - rr * 1.1, xx + rr, yy + rr * 1.1],
                       fill=(250, 206, 52, 255), outline=(198, 146, 16, 255), width=S)
            cd.ellipse([xx - rr * 0.30, yy - rr * 0.45, xx + rr * 0.10, yy],
                       fill=(255, 240, 150, 255))
    cob.putalpha(Image.composite(body, Image.new("L", (w, h), 0), body))
    im.alpha_composite(cob)

    # 苞叶（两片从底部包上来）
    for sgn in (-1, 1):
        d.polygon([(bx + sgn * cob_w * 0.40, by + cob_h * 0.86),
                   (bx + sgn * cob_w * 0.62, by + cob_h * 0.30),
                   (bx + sgn * cob_w * 0.10, by + cob_h * 0.99)], fill=g_l + (255,))
        d.line([(bx + sgn * cob_w * 0.40, by + cob_h * 0.90),
                (bx + sgn * cob_w * 0.56, by + cob_h * 0.38)], fill=g_d + (255,), width=S)
    # 顶须
    d.line([(bx, by + cob_h * 0.06), (bx - w * 0.03, by - h * 0.05)], fill=(206, 172, 108, 255), width=S * 2)
    return im.resize((w // S, h // S), Image.LANCZOS)


# ------------------------------------------------------------------ 时间线
T_HOLD_END = 1.55     # 背对镜头掰玉米
T_NOTICE = (1.55, 1.95)   # 察觉：头部一顿、身体缩一下
T_TURN = (1.95, 2.32)     # 转身（横向挤压换图）
T_SETTLE = (2.40, 2.80)
T_RAISE = (2.80, 3.25)
T_WAVE_START = 3.25


def timeline(t):
    """返回该时刻的 (视图, 形变参数, 挤压系数, 帽跳)"""
    p = t / DUR
    A = dict(arm_l=0.0, arm_r=0.0, tilt=1.6 * math.sin(2 * math.pi * t / 5.2),
             squash=0.0, hop=0.0, lean=0.0, p=p)
    squeeze = 1.0
    hat_jump = 0.0
    view = "back"

    # 基础呼吸/摆动（全时段）
    A["squash"] += 0.005 * math.sin(2 * math.pi * t / 3.9)
    A["hop"] += 1.6 * math.sin(2 * math.pi * t / 3.4)

    if t < T_NOTICE[0]:
        view = "back"
        A["arm_r"] = -0.10 - 0.05 * math.sin(2 * math.pi * t / 4.4)   # 扶着玉米的微动
        A["tilt"] += 1.8 * math.sin(2 * math.pi * t / 2.7)
        A["hop"] += 1.2 * math.sin(2 * math.pi * t / 2.6)

    elif t < T_TURN[0]:
        view = "back"
        k = seg(t, *T_NOTICE)
        A["tilt"] += -8.0 * ease(k, "out") * (1 - 0.5 * ease(seg(t, T_NOTICE[0] + 0.2, T_NOTICE[1])))
        A["squash"] += 0.020 * math.sin(math.pi * k)
        A["lean"] = 0.022 * math.sin(math.pi * k)
        hat_jump = -7.0 * math.sin(math.pi * k)
        A["hop"] += -4.0 * math.sin(math.pi * k)

    elif t < T_TURN[1]:
        u = seg(t, *T_TURN)
        A["tilt"] += 3.0 * math.sin(math.pi * u)
        A["hop"] += -8.0 * math.sin(math.pi * u)
        A["squash"] += 0.02 * math.sin(math.pi * u)
        squeeze = 1.0 - 0.42 * math.sin(math.pi * u) ** 0.8
        view = "back" if u < 0.50 else "front"
        hat_jump = -8.0 * math.sin(math.pi * u)

    elif t < T_SETTLE[1]:
        view = "front"
        k = seg(t, *T_SETTLE)
        A["hop"] += 6.0 * math.sin(math.pi * k) * (1 - k)
        A["squash"] += 0.012 * math.sin(math.pi * k)
        A["tilt"] += 2.0 * (1 - k)

    elif t < T_RAISE[1]:
        view = "front"
        k = ease(seg(t, *T_RAISE), "out")
        A["arm_l"] = -1.25 * k
        A["tilt"] += -2.4 * k
        A["hop"] += -5.0 * math.sin(math.pi * k)

    else:
        view = "front"
        A["arm_l"] = -1.25 - 0.28 * math.sin(2 * math.pi * 2.2 * (t - T_WAVE_START))
        A["arm_r"] = -0.10 + 0.06 * math.sin(2 * math.pi * 2.2 * (t - T_WAVE_START) + 1.0)
        A["tilt"] += -2.4 + 1.8 * math.sin(2 * math.pi * t / 3.4)
        A["hop"] += 2.4 * math.sin(2 * math.pi * t / 2.1)
        A["squash"] += 0.007 * math.sin(2 * math.pi * t / 2.1)
        hat_jump = 2.5 * math.sin(2 * math.pi * t / 2.1)
    return view, A, squeeze, hat_jump


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", default=r"C:/Users/咸鱼一条/Desktop/小企鹅.png")
    ap.add_argument("--bg", default=os.path.join(REPO, "assets", "bg_cornfield.png"))
    ap.add_argument("--out", default=r"D:/aicg/_penguin/penguin_corn_turn.mp4")
    ap.add_argument("--dur", type=float, default=DUR)
    ap.add_argument("--fps", type=int, default=FPS)
    a = ap.parse_args()

    dur, fps = a.dur, a.fps
    NF = int(round(dur * fps))
    tmp = os.path.join(os.path.dirname(os.path.abspath(a.out)), "_frames")
    os.makedirs(tmp, exist_ok=True)

    views = split_sheet(a.sheet)
    names = ["front", "side", "back"]
    # 统一到同一身高、脚底对齐
    CH_H = int(CANVAS * 0.68)
    norm = {}
    for n, im in zip(names, views):
        s = CH_H / im.height
        norm[n] = im.resize((max(1, int(im.width * s)), CH_H), Image.LANCZOS)
    hat = make_straw_hat(int(norm["front"].width * 0.78))
    hat_brim_center = hat.height - int(hat.width * 0.26 * 0.5)
    corn = make_corn_stalk(int(CANVAS * 0.56))

    bg = Image.open(a.bg).convert("RGB")
    sc = max(CANVAS / bg.width, CANVAS / bg.height) * 1.18
    bg = bg.resize((int(bg.width * sc), int(bg.height * sc)), Image.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(0.7))

    css = {}
    for i in range(NF):
        t = i / fps
        view, A, squeeze, hat_jump = timeline(t)
        ch = norm[view]

        # 正机位用形变场（挥手/呼吸），背/侧机位只轻微形变
        if view == "front":
            body = _warp(ch, t, dict(sway=0.008, sway_T=5.2, breath=0.010, breath_T=3.9), A)
        else:
            A2 = dict(A)
            A2["arm_l"] = A2["arm_r"] = 0.0
            body = _warp(ch, t, dict(sway=0.013, sway_T=4.6, breath=0.011, breath_T=3.9), A2)

        # 草帽跟着头部倾角（绕帽檐中心旋转） + 帽跳
        hat_i = hat
        if abs(A["tilt"]) > 0.05:
            hat_i = hat.rotate(-A["tilt"] * 0.85, resample=Image.BICUBIC,
                               center=(hat.width / 2, hat_brim_center))
        if squeeze < 1.0:
            body = body.resize((max(1, int(body.width * squeeze)), body.height), Image.LANCZOS)
            hat_i = hat_i.resize((max(1, int(hat_i.width * squeeze)), hat_i.height),
                                 Image.LANCZOS)

        frame = Image.new("RGBA", (CANVAS, CANVAS), (18, 22, 16, 255))
        bx = (bg.width - CANVAS) // 2
        by = int((bg.height - CANVAS) * 0.45)
        frame.alpha_composite(bg.crop((bx, by, bx + CANVAS, by + CANVAS)).convert("RGBA"))

        ground = CANVAS - 60
        # 地面接触阴影
        sh = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
        ImageDraw.Draw(sh).ellipse([CANVAS * 0.32, ground - 34, CANVAS * 0.68, ground + 26],
                                   fill=(28, 22, 12, 120))
        frame.alpha_composite(sh.filter(ImageFilter.GaussianBlur(12)))

        # 角色落位
        cx = (CANVAS - body.width) // 2
        cy = ground - body.height + int(A["hop"])

        # 玉米秆（世界坐标：在角色右侧，棒子对着她的鳍）；掰的那一刻往她那边拽几像素
        pull = 0.0
        if t < T_NOTICE[0]:
            pull = 10.0 * math.sin(math.pi * min(1.0, max(0.0, (t - 0.55) / 0.85)))
        cob_x = cx + int(body.width * 0.86)
        corn_x = cob_x - int(corn.width * 0.64) - int(pull)
        corn_y = ground - corn.height + 30 + int(3.0 * math.sin(2 * math.pi * t / 4.6))
        frame.alpha_composite(corn, (corn_x, corn_y))

        if T_TURN[0] <= t < T_TURN[1]:
            u3 = seg(t, *T_TURN)
            body = body.rotate(4.5 * math.sin(2 * math.pi * u3) * 0.5, resample=Image.BICUBIC,
                               center=(body.width / 2, body.height * 0.9))

        # 转身最快处加横向拖影（比侧面缩成一条更像"唰地转过来"）
        smear = 0.0
        if T_TURN[0] <= t < T_TURN[1]:
            u2 = seg(t, *T_TURN)
            smear = 9.0 * math.sin(math.pi * u2) ** 1.2

        # 先角色后草帽（帽檐压在头顶上，看起来才像"戴"着）
        if smear > 0.6:
            for k, wgt in ((-1.0, 0.30), (1.0, 0.30)):
                ghost = body.copy()
                ghost.putalpha(ghost.getchannel("A").point(lambda v: int(v * wgt)))
                frame.alpha_composite(ghost, (cx + int(smear * k), cy))
        frame.alpha_composite(body, (cx, cy))
        hat_x = cx + (body.width - hat_i.width) // 2
        hat_y = cy + int(CH_H * 0.105) - hat_brim_center + int(hat_jump)
        frame.alpha_composite(hat_i, (hat_x, hat_y))

        # 推镜：成片窗口
        z = 1.0 + 0.075 * ease(t / dur) + 0.004 * math.sin(2 * math.pi * t / 2.4)
        w = int(OUT / z)
        wx = cx + body.width // 2 - w // 2
        wy = int(CANVAS * 0.50) - int(w * 0.52)
        wx = max(0, min(CANVAS - w, wx))
        wy = max(0, min(CANVAS - w, wy))
        frame = frame.crop((wx, wy, wx + w, wy + w)).resize((OUT, OUT), Image.LANCZOS)
        frame.convert("RGB").save(os.path.join(tmp, f"f{i:05d}.png"))
        if i % 24 == 0:
            print(f"frame {i}/{NF}", flush=True)

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    subprocess.run([quality.FFMPEG, "-y", "-v", "error", "-framerate", str(fps),
                    "-i", os.path.join(tmp, "f%05d.png"), "-c:v", "libx264",
                    "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", a.out], check=True)
    print("DONE", a.out, os.path.getsize(a.out), flush=True)
    print(quality.coherence(a.out, sheet=os.path.splitext(a.out)[0] + "_sheet.png"))


if __name__ == "__main__":
    main()
