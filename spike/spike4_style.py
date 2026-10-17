#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CP1-spike 之四：风格选型 + 放大到交付尺寸
 ① 三个 SD1.5 纯动漫底模各出 1 张（512x896）比风格：谁更"平涂+硬线+高饱和"漫剧风
 ② 把其中一张用 anime 向 ESRGAN 放大到 ≈720x1280，测耗时与清晰度
输出 spike-out/style/ + style.json + 联络表 style-sheet.jpg
"""
import io
import json
import os
import statistics
import sys
import time

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from core.sdclient import SD                        # noqa: E402

OUT = os.path.join(HERE, "spike-out", "style")
os.makedirs(OUT, exist_ok=True)

W, H = 512, 896
POS = ("1boy, solo, young man, short messy black hair, scar on cheek, white shirt, "
       "standing in a flower shop, flowers, warm sunlight, upper body, "
       "anime, cel shading, flat color, bold outlines, high saturation, "
       "clean lineart, masterpiece, best quality")
NEG = ("lowres, bad anatomy, bad hands, worst quality, watermark, text, signature, "
       "extra digits, realistic, 3d, photo, blurry")

CAND = ["墨幽2.5D", "AWPainting", "Dark Sushi", "ReVAnimated"]


def sheet(images, labels, path, thumb_w=300):
    ims = [Image.open(p).convert("RGB") for p in images]
    th = [im.resize((thumb_w, int(im.height * thumb_w / im.width))) for im in ims]
    top = max(t.height for t in th)
    canvas = Image.new("RGB", (thumb_w * len(th), top + 32), (24, 24, 28))
    d = ImageDraw.Draw(canvas)
    for i, (t, lb) in enumerate(zip(th, labels)):
        canvas.paste(t, (i * thumb_w, 32))
        d.text((i * thumb_w + 6, 10), lb, fill=(255, 230, 120))
    canvas.save(path, quality=88)
    return path


def main():
    sd = SD()
    if not sd.alive():
        print("!! SD 未就绪")
        return 1

    ups = [u["name"] for u in sd.get("/sdapi/v1/upscalers")]
    anime_up = next((u for u in ups if "anime" in u.lower()), None) or \
               next((u for u in ups if "esrgan" in u.lower()), None) or \
               (ups[0] if ups else None)
    print("放大模型候选:", [u for u in ups if "anime" in u.lower() or "esrgan" in u.lower()])
    print("选用:", anime_up, "\n")

    made, labels, results = [], [], {}
    produced = {}
    for name in CAND:
        ckpt = sd.pick_checkpoint(name)
        if not ckpt:
            print(f"× 找不到底模 {name}")
            continue
        sd.set_checkpoint(ckpt)
        t0 = time.time()
        png = sd.txt2img(POS, NEG, w=W, h=H, steps=24, cfg=7.0,
                         sampler="DPM++ 2M", seed=2024)[0]
        dt = time.time() - t0
        p = sd.save(png, os.path.join(OUT, f"{name}_24步.png"))
        made.append(p); labels.append(f"{name} {dt:.1f}s")
        produced[name] = p
        results[name] = {"time": round(dt, 2), "ckpt": ckpt, "file": p}
        print(f"[{name}] {dt:6.2f}s  -> {p}")

    if made:
        sheet(made, labels, os.path.join(HERE, "spike-out", "style-sheet.jpg"))
        print("\n风格联络表:", os.path.join(HERE, "spike-out", "style-sheet.jpg"))

    # —— 放大测试：拿第一个成功的结果 ——
    up_info = {}
    if produced and anime_up:
        name, path = next(iter(produced.items()))
        t0 = time.time()
        res = sd.post("/sdapi/v1/extra-single-image", {
            "image": "data:image/png;base64," +
                     __import__("base64").b64encode(open(path, "rb").read()).decode(),
            "upscaling_resize": 1.5,
            "upscaler_1": anime_up,
            "resize_mode": 0,
        }, timeout=900)
        dt = time.time() - t0
        raw = res.get("image", "")
        b = __import__("base64").b64decode(raw.split(",", 1)[-1])
        up_path = os.path.join(OUT, f"{name}_放大{anime_up}.png")
        with open(up_path, "wb") as f:
            f.write(b)
        im = Image.open(up_path)
        up_info = {"source": path, "upscaler": anime_up, "time": round(dt, 2),
                   "size": f"{im.width}x{im.height}", "file": up_path}
        print(f"[放大] {anime_up} x1.5  {dt:.2f}s  -> {im.width}x{im.height}  {up_path}")

    with open(os.path.join(HERE, "spike-out", "style.json"), "w", encoding="utf-8") as f:
        json.dump({"size": f"{W}x{H}", "prompt": POS, "results": results,
                   "upscale": up_info,
                   "available_upscalers": ups}, f, ensure_ascii=False, indent=2)
    print("\n已写:", os.path.join(HERE, "spike-out", "style.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
