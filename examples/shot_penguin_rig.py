#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分件木偶（part rig）：按用户蓝笔标注把头 / 身体 / 手 / 脚拆成可独立旋转的部件。

蓝笔标注 → 骨架：
    头    = 脸周围的圈（只用来定位；真正的头片按剪影最窄处=脖子切）
    身体  = 脊柱竖线（脖子→胯）
    手    = 肩线两端到最外的斜线 → 用「胶囊」把两侧鳍圈出来
    脚    = 底部两条竖线 → 底部横带

关键取舍：
    身体片不挖洞（鳍是"贴片"盖在身体上），这样鳍转走时下面还是完整的身体。
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
from core.motion import ease, seg                        # noqa: E402
from core import quality                                 # noqa: E402
from examples.shot_penguin_corn import (                 # noqa: E402
    make_straw_hat, make_corn_stalk, clean_sprite,
)

CANVAS, OUT, FPS, DUR, PAD = 900, 720, 24, 7.0, 150


# ------------------------------------------------------------------ 蓝笔
def blue_mask(im):
    a = np.asarray(im.convert("RGB")).astype(np.int16)
    R, G, B = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    return (B > 95) & (B - R > 22) & (B - G > 12)


def inpaint(arr, mask, iters=14):
    img = arr.astype(np.float32)
    m = mask.copy()
    h, w = m.shape
    for _ in range(iters):
        if not m.any():
            break
        known = (~m).astype(np.float32)
        acc = np.zeros_like(img)
        cnt = np.zeros((h, w), dtype=np.float32)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy == 0 and dx == 0:
                    continue
                ys, xs = slice(max(0, dy), h + min(0, dy)), slice(max(0, dx), w + min(0, dx))
                ys2, xs2 = slice(max(0, -dy), h + min(0, -dy)), slice(max(0, -dx), w + min(0, -dx))
                acc[ys, xs] += img[ys2, xs2] * known[ys2, xs2][:, :, None]
                cnt[ys, xs] += known[ys2, xs2]
        fill = m & (cnt > 0)
        for c in range(3):
            img[:, :, c][fill] = acc[:, :, c][fill] / cnt[fill]
        m = m & (~fill)
    # 填完再局部糊一下，避免留下「补丁纹」
    out = Image.fromarray(img.clip(0, 255).astype(np.uint8))
    soft = out.filter(ImageFilter.GaussianBlur(1.8))
    m3 = Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(7))
    a = np.asarray(m3).astype(np.float32)[:, :, None] / 255.0 * 0.75
    res = np.asarray(out).astype(np.float32) * (1 - a) + np.asarray(soft).astype(np.float32) * a
    return res.clip(0, 255).astype(np.uint8), m


def read_guide(sheet):
    """从蓝笔里读关节（sheet 坐标）"""
    m = blue_mask(sheet)
    m[:, 520:] = False
    ys, xs = np.nonzero(m)
    if not len(xs):
        raise SystemExit("没找到蓝色标注")
    head_yy = np.nonzero(m[:400].sum(axis=1) > 0)[0]
    head_xx = np.nonzero(m[:400].sum(axis=0) > 0)[0]
    spine = m[:, 268:280]
    sy = np.nonzero(spine.sum(axis=1) > 0)[0]
    runs, start, prev = [], sy[0], sy[0]
    for y in sy[1:]:
        if y - prev > 3:
            runs.append((start, prev))
            start = y
        prev = y
    runs.append((start, prev))
    spine_run = max(runs, key=lambda r: r[1] - r[0])
    band = m[478:494, :]
    sx = np.nonzero(band.sum(axis=0) > 0)[0]
    low = m[555:720, :]
    ly, lx = np.nonzero(low)
    order = np.argsort(lx)
    lx, ly = lx[order], ly[order]
    clusters, cur = [], [0]
    for i in range(1, len(lx)):
        if lx[i] - lx[cur[-1]] > 14:
            clusters.append(cur)
            cur = [i]
        cur.append(i)
    clusters.append(cur)
    feet = [((float(lx[np.array(c)].mean())), float(ly[np.array(c)].max() + 555)) for c in clusters]
    return dict(head_cx=float((head_xx.min() + head_xx.max()) / 2),
                head_bottom=float(head_yy.max()),
                neck=(float((head_xx.min() + head_xx.max()) / 2), float(spine_run[0])),
                hip=(273.0, float(spine_run[1])),
                shoulder_y=484.0,
                shoulder_span=(float(sx.min()), float(sx.max())),
                hand_l=(118.0, 527.0), hand_r=(442.0, 514.0),
                feet=feet, sprite_h=float(sheet.height))


def split_views(sheet):
    src = sheet.convert("RGB")
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
    out = []
    for x0, x1 in runs[:3]:
        rx0 = max(0, x0 - 10)
        raw = src.crop((rx0, 0, min(src.width, x1 + 10), src.height))
        m = blue_mask(raw)
        arr = np.asarray(raw)
        if m.any():
            arr, _ = inpaint(arr, m)
            raw = Image.fromarray(arr)
        cut, bbox = clean_sprite(raw, with_bbox=True)
        out.append((cut, (rx0 + bbox[0], bbox[1])))
    return out




def view0_map(guide, off, src_h, dst_h):
    """把蓝笔关节（sheet 坐标）换算成正面 sprite 坐标（目标身高 dst_h）"""
    sc = dst_h / src_h

    def cv(pt):
        return ((pt[0] - off[0]) * sc, (pt[1] - off[1]) * sc)
    neck = cv((guide["neck"][0], guide["head_bottom"] + 6))
    hand_l = cv(guide["hand_l"])
    hand_r = cv(guide["hand_r"])
    el_l = cv((guide["shoulder_span"][0] + 70, guide["shoulder_y"] + 6))
    el_r = cv((guide["shoulder_span"][1] - 70, guide["shoulder_y"] + 6))
    return dict(neck=neck, hand_l=hand_l, hand_r=hand_r, elbow_l=el_l, elbow_r=el_r)


# ------------------------------------------------------------------ 分件
def capsule_mask(shape, p0, p1, r):
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    ax, ay = p0
    bx, by = p1
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 < 1e-6:
        t = np.zeros_like(xx)
    else:
        t = np.clip(((xx - ax) * dx + (yy - ay) * dy) / L2, 0, 1)
    px, py = ax + t * dx, ay + t * dy
    return ((xx - px) ** 2 + (yy - py) ** 2) <= r * r


def silhouette_neck(sprite):
    a = np.asarray(sprite)
    al = a[:, :, 3] > 8
    H, W = al.shape
    widths = np.array([(np.nonzero(al[y])[0].max() - np.nonzero(al[y])[0].min() + 1)
                       if al[y].any() else 0 for y in range(H)])
    lo, hi = int(H * 0.40), int(H * 0.60)
    return lo + int(np.argmin(widths[lo:hi])), H, W


def build_parts(sprite, guide_pts, r_fin):
    W, H = sprite.size
    big = Image.new("RGBA", (W + PAD * 2, H + PAD * 2), (0, 0, 0, 0))
    big.alpha_composite(sprite, (PAD, PAD))
    a = np.asarray(big)
    alpha = a[:, :, 3] > 8
    yy = np.mgrid[0:big.height, 0:big.width][0].astype(np.int32)

    neck_y = silhouette_neck(sprite)[0] + PAD
    foot_y = int(H * 0.925) + PAD

    head = alpha & (yy <= neck_y + 18)
    below = alpha & (yy > neck_y + 18)
    feet = below & (yy >= foot_y)
    body = below & (~feet)
    def _sp(pt):
        return (pt[0] + PAD, pt[1] + PAD)

    fin_l = alpha & capsule_mask(alpha.shape, _sp(guide_pts["p_l0"]), _sp(guide_pts["p_l1"]), r_fin)
    fin_r = alpha & capsule_mask(alpha.shape, _sp(guide_pts["p_r0"]), _sp(guide_pts["p_r1"]), r_fin)

    layers = {}
    for k, m in (("head", head), ("body", body), ("feet", feet),
                 ("fin_l", fin_l), ("fin_r", fin_r)):
        layer = Image.new("RGBA", big.size, (0, 0, 0, 0))
        layer.paste(big, (0, 0), Image.fromarray((m * 255).astype(np.uint8)))
        layers[k] = layer

    piv = dict(neck=(guide_pts["neck"][0] + PAD, neck_y),
               shoulder_l=_sp(guide_pts["p_l0"]), shoulder_r=_sp(guide_pts["p_r0"]),
               hip=(W / 2 + PAD, neck_y + 0.62 * (foot_y - neck_y)),
               ground=(W / 2 + PAD, H + PAD))
    return layers, piv


def rot(layer, deg, pivot):
    if abs(deg) < 0.02:
        return layer
    return layer.rotate(-deg, resample=Image.BICUBIC, center=pivot)


def compose(layers, piv, pose, hat_layer=None):
    W, H = layers["body"].size
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    br = pose.get("body_rot", 0.0)
    img.alpha_composite(rot(layers["feet"], br * 0.3, piv["hip"]))
    img.alpha_composite(rot(layers["body"], br, piv["hip"]))
    img.alpha_composite(rot(layers["head"], pose.get("head_rot", 0.0), piv["neck"]))
    if hat_layer is not None:
        img.alpha_composite(rot(hat_layer, pose.get("head_rot", 0.0), piv["neck"]))
    # 鳍最后画：举起来时才会盖在头/身前面，而不是被大头挡住
    img.alpha_composite(rot(layers["fin_l"], pose.get("fl_l", 0.0), piv["shoulder_l"]))
    img.alpha_composite(rot(layers["fin_r"], pose.get("fl_r", 0.0), piv["shoulder_r"]))
    return img


# ------------------------------------------------------------------ 时间线
T_NOTICE, T_TURN = (1.55, 1.95), (1.95, 2.32)
T_SETTLE, T_RAISE, T_WAVE = (2.32, 2.80), (2.80, 3.25), 3.25


def timeline(t, dur):
    p = min(max(t / dur, 0.0), 1.0)
    pose = dict(head_rot=0.0, body_rot=0.0, fl_l=0.0, fl_r=0.0)
    view, squeeze = "back", 1.0
    pose["body_rot"] = 1.1 * math.sin(2 * math.pi * t / 4.4)
    pose["head_rot"] = 1.6 * math.sin(2 * math.pi * t / 4.4 + 0.9)
    yoff = 1.2 * math.sin(2 * math.pi * t / 3.6)

    if t < T_NOTICE[0]:
        pose["fl_r"] = 10 + 5 * math.sin(2 * math.pi * t / 4.4)
    elif t < T_TURN[0]:
        k = seg(t, *T_NOTICE)
        pose["head_rot"] += -9.0 * ease(k, "out") * (1 - 0.45 * ease(seg(t, T_NOTICE[0] + 0.22, T_NOTICE[1])))
        pose["body_rot"] += -2.5 * ease(k, "out")
        yoff += -4.0 * math.sin(math.pi * k)
        pose["fl_r"] = 10
    elif t < T_TURN[1]:
        u = seg(t, *T_TURN)
        pose["head_rot"] += 4.0 * math.sin(math.pi * u)
        pose["body_rot"] += 2.0 * math.sin(math.pi * u)
        yoff += -8.0 * math.sin(math.pi * u)
        squeeze = 1.0 - 0.40 * math.sin(math.pi * u) ** 0.8
        view = "back" if u < 0.5 else "front"
    elif t < T_SETTLE[1]:
        view = "front"
        k = seg(t, *T_SETTLE)
        yoff += 6.0 * math.sin(math.pi * k) * (1 - k)
        pose["head_rot"] += 6.0 * (1 - k)
    elif t < T_RAISE[1]:
        view = "front"
        k = ease(seg(t, *T_RAISE), "out")
        pose["fl_l"] = 46.0 * k
        pose["head_rot"] += -4.0 * k
        yoff += -5.0 * math.sin(math.pi * k)
    else:
        view = "front"
        pose["fl_l"] = 46.0 - 13.0 * math.sin(2 * math.pi * 2.2 * (t - T_WAVE))
        pose["fl_r"] = 5.0 * math.sin(2 * math.pi * 2.2 * (t - T_WAVE) + 1.2)
        pose["head_rot"] += -4.0 + 3.0 * math.sin(2 * math.pi * t / 3.2)
        pose["body_rot"] += 1.2 * math.sin(2 * math.pi * t / 2.2)
        yoff += 2.4 * math.sin(2 * math.pi * t / 2.2)
    return view, pose, squeeze, yoff


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", default=r"C:/Users/咸鱼一条/Desktop/小企鹅.png")
    ap.add_argument("--bg", default=os.path.join(REPO, "assets", "bg_cornfield.png"))
    ap.add_argument("--out", default=r"D:/aicg/_penguin/penguin_rig.mp4")
    ap.add_argument("--debug", action="store_true")
    a = ap.parse_args()

    sheet = Image.open(a.sheet)
    guide = read_guide(sheet)
    views = split_views(sheet)
    CH_H = int(CANVAS * 0.68)

    rigs, widths = [], []
    f_im, f_off = views[0]
    f_sz = (max(1, int(f_im.width * CH_H / f_im.height)), CH_H)
    base = view0_map(guide, f_off, f_im.height, CH_H)

    for name, (im, off) in zip(["front", "side", "back"], views):
        small = im.resize((max(1, int(im.width * CH_H / im.height)), CH_H), Image.LANCZOS)
        Wv, Hv = small.size
        mirror = (name == "back")

        def mv(pt):
            """正面 sheet 坐标 → 该视图 sprite 坐标（按身高比例 + 可选左右镜像）"""
            fx = (pt[0] - f_off[0] - f_sz[0] / 2) / f_sz[1]
            fy = (pt[1] - f_off[1]) / f_sz[1]
            if mirror:
                fx = -fx
            return (Wv / 2 + fx * Hv, fy * Hv)

        p_l0, p_l1 = mv(base["elbow_l"]), mv(base["hand_l"])
        p_r0, p_r1 = mv(base["elbow_r"]), mv(base["hand_r"])
        if mirror:
            p_l0, p_l1, p_r0, p_r1 = p_r0, p_r1, p_l0, p_l1
        pts = dict(p_l0=p_l0, p_l1=p_l1, p_r0=p_r0, p_r1=p_r1, neck=mv(base["neck"]))
        layers, piv = build_parts(small, pts, r_fin=CH_H * 0.085)
        rigs.append((layers, piv))
        widths.append(Wv)

    if a.debug:
        W0 = max(r[0]["body"].size[0] for r in rigs)
        H0 = rigs[0][0]["body"].size[1]
        out = Image.new("RGB", (W0 * 6, H0 * 1), (24, 26, 30))
        x = 0
        layers, piv = rigs[0]
        for k in ("head", "body", "fin_l", "fin_r", "feet"):
            out.paste(layers[k].convert("RGB"), (x, 0))
            ImageDraw.Draw(out).text((x + 6, 6), k, fill=(255, 240, 120))
            x += W0
        out.paste(compose(layers, piv, {}).convert("RGB"), (x, 0))
        out.save(r"D:/aicg/_penguin/rig_parts.png")
        print("debug -> rig_parts.png")
        return

    dur, fps = DUR, FPS
    NF = int(round(dur * fps))
    tmp = os.path.join(os.path.dirname(os.path.abspath(a.out)), "_rigframes")
    os.makedirs(tmp, exist_ok=True)
    for f in os.listdir(tmp):
        os.remove(os.path.join(tmp, f))

    hat = make_straw_hat(int(widths[0] * 0.78))
    hat_brim_center = hat.height - int(hat.width * 0.26 * 0.5)
    hat_layers = []
    for layers, piv in rigs:
        Wc, Hc = layers["body"].size
        canvas = Image.new("RGBA", (Wc, Hc), (0, 0, 0, 0))
        hx = (Wc - hat.width) // 2
        hy = int(PAD + CH_H * 0.105 - hat_brim_center)
        canvas.alpha_composite(hat, (hx, hy))
        hat_layers.append(canvas)
    corn = make_corn_stalk(int(CANVAS * 0.56))

    bg = Image.open(a.bg).convert("RGB")
    sc_bg = max(CANVAS / bg.width, CANVAS / bg.height) * 1.18
    bg = bg.resize((int(bg.width * sc_bg), int(bg.height * sc_bg)), Image.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(0.7))
    bx0, by0 = (bg.width - CANVAS) // 2, int((bg.height - CANVAS) * 0.45)
    bgc = bg.crop((bx0, by0, bx0 + CANVAS, by0 + CANVAS)).convert("RGBA")

    ground = CANVAS - 60
    shadow = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse([CANVAS * 0.33, ground - 30, CANVAS * 0.67, ground + 24],
                                   fill=(26, 20, 10, 130))
    shadow = shadow.filter(ImageFilter.GaussianBlur(12))

    for i in range(NF):
        t = i / fps
        view, pose, squeeze, yoff = timeline(t, dur)
        layers, piv = rigs[{"front": 0, "side": 1, "back": 2}[view]]
        ch = compose(layers, piv, pose, hat_layer=hat_layers[{"front": 0, "side": 1, "back": 2}[view]])

        if squeeze < 1.0:
            ch = ch.resize((max(1, int(ch.width * squeeze)), ch.height), Image.LANCZOS)

        frame = bgc.copy()
        frame.alpha_composite(shadow)

        cx = (CANVAS - widths[0]) // 2 - PAD + (widths[0] - (ch.width - PAD * 2)) // 2
        cy = ground - (ch.height - PAD * 2) + int(yoff) - PAD

        pull = 0.0
        if t < T_NOTICE[0]:
            pull = 9.0 * math.sin(math.pi * min(1.0, max(0.0, (t - 0.55) / 0.85)))
        cob_x = CANVAS // 2 + int(widths[0] * 0.36)
        corn_x = cob_x - int(corn.width * 0.64) - int(pull)
        corn_y = ground - corn.height + 30 + int(3.0 * math.sin(2 * math.pi * t / 4.6))
        frame.alpha_composite(corn, (corn_x, corn_y))

        smear = 0.0
        if T_TURN[0] <= t < T_TURN[1]:
            u = seg(t, *T_TURN)
            smear = 10.0 * math.sin(math.pi * u) ** 1.2
        if smear > 0.6:
            for sgn in (-1.0, 1.0):
                ghost = ch.copy()
                ghost.putalpha(ghost.getchannel("A").point(lambda v: int(v * 0.28)))
                frame.alpha_composite(ghost, (cx + int(smear * sgn), cy))
        frame.alpha_composite(ch, (cx, cy))

        head_cx = cx + PAD + widths[0] // 2

        z = 1.0 + 0.075 * ease(t / dur) + 0.004 * math.sin(2 * math.pi * t / 2.4)
        w = int(OUT / z)
        wx = int(head_cx - w // 2)
        wy = int(CANVAS * 0.50 - w * 0.52)
        wx = max(0, min(CANVAS - w, wx))
        wy = max(0, min(CANVAS - w, wy))
        frame.crop((wx, wy, wx + w, wy + w)).resize((OUT, OUT), Image.LANCZOS).convert("RGB").save(
            os.path.join(tmp, f"f{i:05d}.png"))
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
