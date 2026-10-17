#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
pillow_ops.py — offline image batch processing for aigc-bridge.

Usage:  python pillow_ops.py '<json spec>'

Spec keys:
  op        resize|crop|cover|thumbnail|watermark|convert|montage|rotate|flip|border|info
  inputs    list of absolute file paths
  out_dir   output directory
  suffix    filename suffix (default "_out")
  width,height,ratio,quality,format,text,image,position,opacity,font_size,columns

Prints a single JSON line: {"outputs": ["<abs path>", ...]}
"""
import json
import os
import sys
from PIL import Image, ImageDraw, ImageFont, ImageOps

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",      # 微软雅黑
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",    # 黑体
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\arial.ttf",
]


def load_font(size):
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def out_path(spec, src, ext=None):
    stem = os.path.splitext(os.path.basename(src))[0]
    ext = ext or os.path.splitext(src)[1][1:] or "png"
    name = f"{stem}{spec.get('suffix', '_out')}.{ext}"
    return os.path.join(spec["out_dir"], name)


def ratio_tuple(ratio, w, h):
    if ratio:
        a, b = ratio.split(":")
        return int(a), int(b)
    return w or 16, h or 9


def op_resize(im, spec):
    w = spec.get("width")
    h = spec.get("height")
    if not w and not h:
        return im
    if w and not h:
        h = round(im.height * w / im.width)
    if h and not w:
        w = round(im.width * h / im.height)
    return im.resize((int(w), int(h)), Image.LANCZOS)


def op_cover(im, spec):
    """Crop to target aspect ratio (centre) then resize — the 封面/缩略图 workhorse."""
    tw, th = ratio_tuple(spec.get("ratio"), spec.get("width"), spec.get("height"))
    target = tw / th
    cur = im.width / im.height
    if cur > target:                       # too wide -> trim sides
        nw = round(im.height * target)
        left = (im.width - nw) // 2
        im = im.crop((left, 0, left + nw, im.height))
    elif cur < target:                     # too tall -> trim top/bottom
        nh = round(im.width / target)
        top = int((im.height - nh) * spec.get("anchor", 0.42))
        im = im.crop((0, top, im.width, top + nh))
    if spec.get("width") or spec.get("height"):
        w = spec.get("width") or round(im.width * (spec.get("height") / im.height))
        h = spec.get("height") or round(im.height * (w / im.width))
        im = im.resize((int(w), int(h)), Image.LANCZOS)
    return im


def op_crop(im, spec):
    w = spec.get("width") or im.width
    h = spec.get("height") or im.height
    left = spec.get("left", max(0, (im.width - w) // 2))
    top = spec.get("top", max(0, (im.height - h) // 2))
    return im.crop((left, top, left + w, top + h))


def op_thumbnail(im, spec):
    im = im.copy()
    im.thumbnail((spec.get("width") or 1280, spec.get("height") or 720), Image.LANCZOS)
    return im


def op_watermark(im, spec):
    im = im.convert("RGBA")
    overlay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    alpha = int(255 * spec.get("opacity", 80) / 100)

    if spec.get("image"):
        wm = Image.open(spec["image"]).convert("RGBA")
        ww = spec.get("width") or max(60, im.width // 6)
        wm = wm.resize((ww, round(wm.height * ww / wm.width)), Image.LANCZOS)
        if alpha < 255:
            a = wm.split()[3].point(lambda v: int(v * alpha / 255))
            wm.putalpha(a)
        pos = spec.get("position", "bottomright")
        m = 24
        xy = {
            "topleft": (m, m),
            "topright": (im.width - wm.width - m, m),
            "bottomleft": (m, im.height - wm.height - m),
            "bottomright": (im.width - wm.width - m, im.height - wm.height - m),
            "center": ((im.width - wm.width) // 2, (im.height - wm.height) // 2),
        }.get(pos, (im.width - wm.width - m, im.height - wm.height - m))
        overlay.alpha_composite(wm, xy)
    elif spec.get("text"):
        font = load_font(int(spec.get("font_size", max(20, im.width // 24))))
        d = ImageDraw.Draw(overlay)
        txt = spec["text"]
        box = d.textbbox((0, 0), txt, font=font)
        tw, th = box[2] - box[0], box[3] - box[1]
        m = max(12, im.width // 50)
        pos = spec.get("position", "bottomright")
        xy = {
            "topleft": (m, m),
            "topright": (im.width - tw - m, m),
            "bottomleft": (m, im.height - th - m),
            "bottomright": (im.width - tw - m, im.height - th - m),
            "center": ((im.width - tw) // 2, (im.height - th) // 2),
        }.get(pos, (im.width - tw - m, im.height - th - m))
        # soft shadow for readability
        d.text((xy[0] + 2, xy[1] + 2), txt, font=font, fill=(0, 0, 0, alpha))
        d.text(xy, txt, font=font, fill=(255, 255, 255, alpha))

    out = Image.alpha_composite(im, overlay)
    return out.convert("RGB")


def op_montage(images, spec):
    cols = int(spec.get("columns", 4))
    cw = spec.get("width") or 480
    ch = spec.get("height") or 270
    rows = (len(images) + cols - 1) // cols
    gap = 8
    sheet = Image.new("RGB", (cols * cw + gap * (cols + 1), rows * ch + gap * (rows + 1)), (18, 18, 18))
    for i, im in enumerate(images):
        t = ImageOps.fit(im.convert("RGB"), (cw, ch), Image.LANCZOS)
        r, c = divmod(i, cols)
        sheet.paste(t, (gap + c * (cw + gap), gap + r * (ch + gap)))
    return sheet


def main():
    spec = json.loads(sys.argv[1])
    op = spec["op"]
    inputs = spec["inputs"]
    os.makedirs(spec["out_dir"], exist_ok=True)
    outputs = []

    if op == "info":
        for p in inputs:
            with Image.open(p) as im:
                outputs.append({
                    "file": p,
                    "size": list(im.size),
                    "mode": im.mode,
                    "format": im.format,
                })
        print(json.dumps({"meta": outputs, "outputs": []}, ensure_ascii=False))
        return

    if op == "montage":
        images = [Image.open(p) for p in inputs]
        sheet = op_montage(images, spec)
        dest = os.path.join(spec["out_dir"], f"montage{spec.get('suffix', '_out')}.jpg")
        sheet.save(dest, quality=int(spec.get("quality", 92)))
        print(json.dumps({"outputs": [dest]}, ensure_ascii=False))
        return

    for p in inputs:
        with Image.open(p) as im:
            im = ImageOps.exif_transpose(im)
            is_jpegish = os.path.splitext(p)[1].lower() in (".jpg", ".jpeg")

            if op == "resize":
                res = op_resize(im, spec)
            elif op == "cover":
                res = op_cover(im, spec)
            elif op == "crop":
                res = op_crop(im, spec)
            elif op == "thumbnail":
                res = op_thumbnail(im, spec)
            elif op == "watermark":
                res = op_watermark(im, spec)
            elif op == "rotate":
                res = im.rotate(float(spec.get("angle", 90)), expand=True)
            elif op == "flip":
                res = ImageOps.mirror(im) if spec.get("axis", "h") == "h" else ImageOps.flip(im)
            elif op == "border":
                res = ImageOps.expand(im, border=int(spec.get("width", 20)), fill=spec.get("color", "black"))
            elif op == "convert":
                res = im.convert("RGB") if spec.get("format", "png").lower() in ("jpg", "jpeg", "webp") else im
            else:
                raise ValueError(f"unknown op: {op}")

            if op == "convert":
                fmt = spec.get("format", "png").lower()
                ext = "jpg" if fmt in ("jpg", "jpeg") else fmt
                dest = out_path(spec, p, ext)
                save_kw = {"quality": int(spec.get("quality", 92))} if ext in ("jpg", "jpeg", "webp") else {}
                res.convert("RGB").save(dest, **save_kw)
            else:
                dest = out_path(spec, p, "jpg" if is_jpegish else "png")
                if dest.lower().endswith((".jpg", ".jpeg")):
                    res.convert("RGB").save(dest, quality=int(spec.get("quality", 92)))
                else:
                    res.save(dest)
            outputs.append(dest)

    print(json.dumps({"outputs": outputs}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}", "outputs": []}, ensure_ascii=False))
        sys.exit(1)
