#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""NeoForgeSD · 动作引擎（立绘木偶 / 形变场驱动）

为什么不是只有 AnimateDiff：
    实测（2026-10-18，RTX 3050 4G）AnimateDiff i2v 在 512×512 只能给出「呼吸级微动」，
    denoise ≥0.6 会掉身份，≤0.5 几乎不动，24 帧后段还会糊。\n剧本动作（抬手/招手/跳）靠它做不到。
    所以动作功能给两条引擎：
      - puppet：单张立绘 + 形变场关键帧 → 动作由参数曲线决定，天然连贯、零显存
      - animatediff：SD API 图生视频 → 氛围微动（在 console/动画后端里走）

用法：
    python core/motion.py --sprite char.png --action wave --dur 3 --fps 24 --out wave.mp4
    python core/motion.py --sprite char.png --action list      # 看全部动作
"""
import argparse
import json
import math
import os
import random
import subprocess
import tempfile

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

COLS, ROWS = 16, 44

ACTIONS = {
    "idle": "待机：呼吸 + 轻微摇摆 + 头部微倾",
    "wave": "招手：右臂抬起并左右挥动 3 次",
    "raise": "举双手：双臂缓慢举过头顶并停住",
    "nod": "点头：头部前后轻点头两次",
    "jump": "跳跃：下蹲蓄力 → 起跳 → 落地压缩回弹",
    "reach": "伸手取物：双臂前伸并微微上提",
    "punch": "出拳：左右手交替向前发力",
    "bow": "鞠躬：上身整体前倾再回正",
}


# ------------------------------------------------------------------ 抠图
MATTE_PY = os.environ.get("MATTE_PY", r"D:\aicg\matting\venv\Scripts\python.exe")
MATTE_SCRIPT = os.environ.get("MATTE_SCRIPT", r"D:\aicg\matting\cutout.py")
_MATTE_WARNED = False


def _matte_alpha(im):
    """BiRefNet 语义抠图（D:\\aicg\\matting\\cutout.py，MIT）。成功返回紧凑 RGBA，失败/不可用返回 None。"""
    global _MATTE_WARNED
    if os.environ.get("NEOFORGE_MATTE", "1") == "0":
        return None
    if not (os.path.exists(MATTE_PY) and os.path.exists(MATTE_SCRIPT)):
        return None
    try:
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "in.png")
            dst = os.path.join(td, "out.png")
            (im if isinstance(im, Image.Image) else Image.open(im)).convert("RGB").save(src)
            r = subprocess.run(
                [MATTE_PY, MATTE_SCRIPT, "matte", src, dst,
                 "--model", os.environ.get("MATTE_MODEL", "isnet"), "--edge-decontam"],
                capture_output=True, timeout=300)
            if r.returncode != 0 or not os.path.exists(dst):
                if not _MATTE_WARNED:
                    _MATTE_WARNED = True
                    print("[motion] matte failed, fallback to white-key:", (r.stderr or b"")[-300:])
                return None
            out = Image.open(dst).convert("RGBA")
            box = out.split()[3].point(lambda v: 255 if v > 8 else 0).getbbox()
            return out.crop(box) if box else out
    except Exception as e:  # 引擎缺失/超时都不该让主流程挂
        if not _MATTE_WARNED:
            _MATTE_WARNED = True
            print("[motion] matte unavailable, fallback to white-key:", e)
        return None


def cutout_white(path, thresh=26):
    """立绘 → 透明底（紧凑裁切）。

    先走 BiRefNet 语义抠图：白底/彩底/复杂底都能扣，边缘是软 alpha、无白边。
    引擎不可用时回退到原来的白底 floodfill（只对近白底有效）。
    """
    src = Image.open(path) if isinstance(path, (str, bytes)) else path
    matted = _matte_alpha(src)
    if matted is not None:
        return matted
    return cutout_white_key(src, thresh)


def cutout_white_key(path, thresh=26):
    """旧做法：白/近白底 floodfill 去底 + MinFilter 收边（仅白底可用，会留白边）。"""
    im = (Image.open(path) if isinstance(path, (str, bytes)) else path).convert("RGB")
    key = (255, 0, 255)
    for xy in [(0, 0), (im.width - 1, 0), (0, im.height - 1), (im.width - 1, im.height - 1)]:
        try:
            ImageDraw.floodfill(im, xy, key, thresh=thresh)
        except Exception:
            pass
    rgba = im.convert("RGBA")
    px = rgba.load()
    for y in range(rgba.height):
        for x in range(rgba.width):
            r, g, b, a = px[x, y]
            if r == 255 and g == 0 and b == 255:
                px[x, y] = (0, 0, 0, 0)
    bbox = rgba.getbbox()
    if not bbox:
        return Image.open(path).convert("RGBA")
    rgba = rgba.crop(bbox)
    alpha = rgba.split()[3].filter(ImageFilter.MinFilter(5)).filter(ImageFilter.GaussianBlur(0.7))
    rgb = rgba.convert("RGB")
    inner = rgb.filter(ImageFilter.MinFilter(3))
    rgb = Image.composite(inner, rgb, alpha.point(lambda v: 255 if v < 250 else 0))
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out.crop(out.getbbox() or (0, 0, out.width, out.height))


# ------------------------------------------------------------------ 缓动
def ease(x, k="in_out"):
    x = min(max(float(x), 0.0), 1.0)
    if k == "in":
        return x * x
    if k == "out":
        return 1 - (1 - x) ** 2
    if k == "none":
        return x
    if k == "back":
        s = 1.70158
        return 1 + (s + 1) * (x - 1) ** 3 + s * (x - 1) ** 2
    if k == "elastic":
        if x in (0.0, 1.0):
            return x
        return 1 - math.pow(2, -9 * x) * math.cos(x * 18)
    return x * x * (3 - 2 * x)


def seg(t, a, b):
    if b <= a:
        return 1.0 if t >= b else 0.0
    return min(max((t - a) / (b - a), 0.0), 1.0)


def _sin(t, T, ph=0.0):
    return math.sin(2 * math.pi * t / T + ph)


# ------------------------------------------------------------------ 动作曲线
def action_params(name, t, dur, P):
    """返回该时刻的形变参数（单位：以角色高度 H 为 1 的归一化比例）"""
    p = min(max(t / dur, 0.0), 1.0)
    arm_l = arm_r = 0.0      # 负=上抬
    tilt = 0.0
    squash = 0.0
    hop = 0.0
    lean = 0.0

    if name == "idle":
        arm_l = 0.035 * _sin(t, 4.1)
        arm_r = 0.035 * _sin(t, 4.1, 0.4)

    elif name == "wave":
        up = ease(seg(p, 0.05, 0.28), "out")          # 抬臂
        down = ease(seg(p, 0.86, 1.0), "in_out")      # 收臂
        arm_r = -0.62 * (up - down)
        arm_r += -0.10 * math.sin(2 * math.pi * 2.4 * max(0.0, p - 0.30)) * up * (1 - down)
        tilt = -1.8 * up + 1.2 * _sin(t, 3.3)

    elif name == "raise":
        up = ease(seg(p, 0.08, 0.55), "in_out")
        arm_l = arm_r = -0.68 * up
        tilt = -1.0 * up
        hop = -4.0 * math.sin(math.pi * seg(p, 0.08, 0.55))

    elif name == "nod":
        k = math.sin(2 * math.pi * 1.35 * max(0.0, p - 0.12)) if p > 0.12 else 0.0
        tilt = 3.2 * k * (1 - ease(seg(p, 0.78, 1.0), "in_out"))

    elif name == "jump":
        crouch = ease(seg(p, 0.05, 0.26), "in_out") * (1 - ease(seg(p, 0.30, 0.40), "out"))
        air = math.sin(math.pi * seg(p, 0.30, 0.72))
        land = ease(seg(p, 0.68, 0.80), "out") * (1 - ease(seg(p, 0.80, 1.0), "back"))
        squash = 0.045 * crouch + 0.035 * land
        hop = -46.0 * air
        arm_l = arm_r = -0.34 * air

    elif name == "reach":
        fw = ease(seg(p, 0.10, 0.55), "in_out")
        arm_l = arm_r = -0.46 * fw
        lean = 0.012 * fw
        tilt = 1.2 * fw

    elif name == "punch":
        a = math.sin(2 * math.pi * 1.1 * p) * (1 - 0.4 * p)
        arm_r = -0.30 * max(0.0, a)
        arm_l = -0.30 * max(0.0, -a)
        squash = 0.012 * abs(a)
        lean = 0.010 * max(0.0, a)

    elif name == "bow":
        b = ease(seg(p, 0.12, 0.42), "in_out") * (1 - ease(seg(p, 0.58, 0.88), "in_out"))
        lean = -0.055 * b
        tilt = 4.5 * b

    return dict(arm_l=arm_l, arm_r=arm_r, tilt=tilt, squash=squash, hop=hop, lean=lean, p=p)


# ------------------------------------------------------------------ 形变场
def _verts(W, H, t, P, A):
    u = np.linspace(0, 1, COLS + 1)[None, :].repeat(ROWS + 1, 0)
    v = np.linspace(0, 1, ROWS + 1)[:, None].repeat(COLS + 1, 1)
    x, y = u * W, v * H
    dx = np.zeros_like(x)
    dy = np.zeros_like(y)

    # 整体摇摆（越靠上越晃）
    dx += P["sway"] * H * ((1 - v) ** 1.7) * _sin(t, P["sway_T"])
    # 全身微起伏（呼吸/重心，保证每一帧都不一样，不出现"定格帧"）
    dy += 0.0045 * H * _sin(t, 3.7)

    # 呼吸（胸口起伏）
    breath = np.exp(-(((v - 0.34) / 0.18) ** 2))
    dy -= P["breath"] * H * breath * (0.5 * (1 + _sin(t, P["breath_T"], -math.pi / 2)))

    # 上身倾斜（tilt 以颈部为轴）+ 身体前倾（lean 以脚底为轴）
    vn = 0.30
    w = np.clip((vn - v) / vn, 0, 1) ** 1.2
    ang = math.radians(A["tilt"] + 1.5 * _sin(t, 6.7, 1.3))
    cx, cy = 0.5 * W, vn * H
    rx, ry = x - cx, y - cy
    ca, sa = math.cos(ang), math.sin(ang)
    dx += w * (rx * ca - ry * sa - rx)
    dy += w * (rx * sa + ry * ca - ry)
    if abs(A["lean"]) > 1e-6:
        k = np.clip(1.0 - v, 0, 1) ** 1.35
        dx += A["lean"] * H * k
        dy += 0.25 * A["lean"] * H * k * (1 - v)

    # 双臂：绕肩关节旋转（不是平移，抬手才不像橡皮拉伸）。负值 = 向上抬
    for uc, key, mir in ((0.16, "arm_l", -1), (0.84, "arm_r", 1)):
        lift = A[key]
        if abs(lift) < 1e-6:
            continue
        px_a = (0.5 + mir * 0.27) * W
        py_a = 0.40 * H
        gu = np.exp(-(((u - uc) / 0.155) ** 2))
        gv = np.exp(-(((v - 0.60) / 0.20) ** 2))
        g = np.clip(gu * gv * 1.7, 0, 1)
        g = g * g * (3 - 2 * g)
        ang = lift * (math.pi / 2) * mir
        ca, sa = math.cos(ang), math.sin(ang)
        rx, ry = x - px_a, y - py_a
        dx += g * (rx * ca - ry * sa - rx)
        dy += g * (rx * sa + ry * ca - ry)

    # 挤压/拉伸（锚点：脚底）
    sq = A["squash"]
    if abs(sq) > 1e-6:
        dy += (y - H) * sq
        dx += (x - 0.5 * W) * (-sq * 0.55)
    return x + dx, y + dy


def _mesh(W, H, t, P, A):
    xs, ys = _verts(W, H, t, P, A)
    xb = [int(round(i * W / COLS)) for i in range(COLS + 1)]
    yb = [int(round(j * H / ROWS)) for j in range(ROWS + 1)]
    out = []
    for j in range(ROWS):
        for i in range(COLS):
            x0, x1, y0, y1 = xb[i], xb[i + 1], yb[j], yb[j + 1]
            if x1 <= x0 or y1 <= y0:
                continue
            q = (float(xs[j, i]), float(ys[j, i]), float(xs[j + 1, i]), float(ys[j + 1, i]),
                 float(xs[j + 1, i + 1]), float(ys[j + 1, i + 1]), float(xs[j, i + 1]), float(ys[j, i + 1]))
            out.append(((x0, y0, x1, y1), q))
    return out


def _warp(img, t, P, A):
    return img.transform(img.size, Image.MESH, _mesh(img.size[0], img.size[1], t, P, A),
                         resample=Image.BILINEAR)


# ------------------------------------------------------------------ 渲染
class Puppet:
    """把一张立绘变成会动的木偶。同一张立绘、同一套参数 → 逐帧确定性输出。"""

    def __init__(self, sprite, size=(720, 720), bg=None, bg_blur=0.6, height=0.72,
                 ground_margin=26, seed=7, cutout=None):
        self.W, self.H = size
        if isinstance(sprite, Image.Image):
            im = sprite.convert("RGBA")
        else:
            im = cutout_white(sprite) if (cutout if cutout is not None else True) else Image.open(sprite).convert("RGBA")
        ch = int(self.H * height)
        self.char = im.resize((max(1, int(im.width * ch / im.height)), ch), Image.LANCZOS)
        self.ground = self.H - self.char.height - ground_margin

        if bg is None:
            self.bg = None
        else:
            b = Image.open(bg).convert("RGB") if not isinstance(bg, Image.Image) else bg.convert("RGB")
            s = max(self.W / b.width, self.H / b.height) * 1.35
            b = b.resize((int(b.width * s), int(b.height * s)), Image.LANCZOS)
            self.bg = b.filter(ImageFilter.GaussianBlur(bg_blur))

        self.P = dict(sway=0.010, sway_T=5.2, breath=0.011, breath_T=3.9)
        self.seed = seed

    def render(self, action="wave", dur=3.0, fps=24, out=None, zoom=0.06):
        if action not in ACTIONS:
            raise ValueError(f"未知动作 {action}，可选：{', '.join(ACTIONS)}")
        NF = max(2, int(round(dur * fps)))
        tmp = tempfile.mkdtemp(prefix="nfpup_")
        rnd = random.Random(self.seed)

        shadow = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).ellipse(
            [self.W * 0.30, self.H - self.char.height * 0.20 - self.ground,
             self.W * 0.70, self.H - self.ground + 6], fill=(28, 20, 8, 105))
        shadow = shadow.filter(ImageFilter.GaussianBlur(14))

        for i in range(NF):
            t = i / fps
            p = i / (NF - 1)
            A = action_params(action, t, dur, self.P)

            if self.bg is None:
                frame = Image.new("RGBA", (self.W, self.H), (16, 18, 22, 255))
            else:
                z = 1.0 + zoom * ease(p)
                bw, bh = self.bg.size
                cw, chh = int(bw / z), int(bh / z)
                x0 = (bw - cw) // 2
                y0 = int((bh - chh) * 0.55)
                frame = self.bg.crop((x0, y0, x0 + cw, y0 + chh)).resize(
                    (self.W, self.H), Image.LANCZOS).convert("RGBA")

            frame.alpha_composite(shadow)
            body = _warp(self.char, t, self.P, A)
            cx = (self.W - body.width) // 2 + int(A["lean"] * 10)
            cy = self.ground + int(A["hop"])
            frame.alpha_composite(body, (cx, cy))
            frame.convert("RGB").save(os.path.join(tmp, f"f{i:04d}.png"))

        out = out or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                                  "outputs", f"motion_{action}.mp4")
        out = os.path.abspath(out)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        from . import quality
        subprocess.run([quality.FFMPEG, "-y", "-v", "error", "-framerate", str(fps),
                        "-i", os.path.join(tmp, "f%04d.png"),
                        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                        "-pix_fmt", "yuv420p", "-movflags", "+faststart", out], check=True)
        return {"path": out, "action": action, "frames": NF, "fps": fps, "dur": dur}


def render(sprite, action="wave", dur=3.0, fps=24, size=(720, 720), bg=None, out=None, seed=7):
    return Puppet(sprite, size=size, bg=bg, seed=seed).render(action, dur=dur, fps=fps, out=out)


def main():
    ap = argparse.ArgumentParser(description="NeoForgeSD 动作引擎（立绘木偶）")
    ap.add_argument("--sprite", help="立绘 PNG（白底或透明底）")
    ap.add_argument("--action", default="wave", help="动作名；--action list 列出全部")
    ap.add_argument("--dur", type=float, default=3.0)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--size", default="720x720")
    ap.add_argument("--bg", default=None, help="背景图（可选）")
    ap.add_argument("--out", default=None)
    ap.add_argument("--metrics", action="store_true", help="出片后跑连贯性体检")
    a = ap.parse_args()

    if a.action == "list" or not a.sprite:
        print("动作模板：")
        for k, v in ACTIONS.items():
            print(f"  {k:8s} {v}")
        return
    w, h = (int(v) for v in a.size.lower().split("x"))
    r = render(a.sprite, a.action, a.dur, a.fps, (w, h), a.bg, a.out)
    print(json.dumps(r, ensure_ascii=False))
    if a.metrics:
        from . import quality
        print(json.dumps(quality.coherence(r["path"]), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
