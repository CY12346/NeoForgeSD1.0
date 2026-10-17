#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CP1-spike 之二：角色一致性三条路对比（底模 = AWPainting，SD1.5 / 512x896）
  A 纯文生图    ：同角色卡 + 换场景换 seed（最省事，脸会漂）
  B img2img派生 ：从定妆图派生 denoise 0.50（换场景，保脸）
  C img2img派生 ：从定妆图派生 denoise 0.35（脸最稳，场景变化小）
输出：spike-out/cast/ + 联络表 consistency-sheet.jpg + consistency.json
判定：由 vision 看联络表后填写 verdict，不自说自话。
"""
import json
import os
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from core.sdclient import SD                        # noqa: E402

OUT = os.path.join(HERE, "spike-out", "cast")
os.makedirs(OUT, exist_ok=True)
W, H = 512, 896
CKPT_HINT = "AWPainting"

CARD = ("1boy, solo, young man, short messy black hair, a short faint scar on "
        "his cheek, pale skin, white shirt, purple eyes")
STYLE = ("anime, cel shading, flat color, bold clean outlines, high saturation, "
         "masterpiece, best quality")
NEG = ("lowres, bad anatomy, bad hands, worst quality, watermark, text, signature, "
       "extra digits, realistic, 3d, photo, blurry, multiple views, character sheet")

PORTRAIT = (f"{CARD}, portrait, upper body, front view, looking at viewer, "
            f"plain warm grey background, {STYLE}")
SCENE = ("standing behind a wooden counter in a small flower shop, buckets of "
         "flowers, warm afternoon sunlight, medium shot, from side")


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
    ckpt = sd.pick_checkpoint(CKPT_HINT)
    if not ckpt:
        print("!! 找不到", CKPT_HINT)
        return 1
    base = sd.set_checkpoint(ckpt)
    print("底模:", base)

    l_hyper = sd.pick_lora("hyper-sd15", "4steps")
    print("加速 LoRA:", l_hyper)
    fast = dict(steps=6, cfg=1.8, sampler="DPM++ SDE",
                loras=[(l_hyper, 1.0)] if l_hyper else None)

    made, labels = [], []

    print("\n[1/4] 定妆图（24步, seed 777）...")
    png = sd.txt2img(PORTRAIT, NEG, w=W, h=H, steps=24, cfg=7.0,
                     sampler="DPM++ 2M", seed=777)[0]
    cast = sd.save(png, os.path.join(OUT, "cast_定妆图_777.png"))
    made.append(cast); labels.append("定妆图 24步")

    print("[2/4] A 纯文生图（同卡换场景换seed 902）...")
    png = sd.txt2img(f"{CARD}, {SCENE}, {STYLE}", NEG, w=W, h=H, seed=902, **fast)[0]
    pA = sd.save(png, os.path.join(OUT, "A_纯文生图_seed902.png"))
    made.append(pA); labels.append("A 纯文生图")

    print("[3/4] B 派生 denoise0.50 ...")
    png = sd.img2img(png0 := open(cast, "rb").read(), f"{CARD}, {SCENE}, {STYLE}",
                     NEG, denoise=0.50, seed=555, **fast)[0]
    pB = sd.save(png, os.path.join(OUT, "B_派生_dn050.png"))
    made.append(pB); labels.append("B 派生 dn0.50")

    print("[4/4] C 派生 denoise0.35 ...")
    png = sd.img2img(png0, f"{CARD}, {SCENE}, {STYLE}", NEG,
                     denoise=0.35, seed=555, **fast)[0]
    pC = sd.save(png, os.path.join(OUT, "C_派生_dn035.png"))
    made.append(pC); labels.append("C 派生 dn0.35")

    sh = sheet(made, labels, os.path.join(HERE, "spike-out", "consistency-sheet.jpg"))
    with open(os.path.join(HERE, "spike-out", "consistency.json"), "w", encoding="utf-8") as f:
        json.dump({"checkpoint": base, "size": f"{W}x{H}", "card": CARD,
                   "scene": SCENE, "fast": {k: v for k, v in fast.items()},
                   "files": {"cast": cast, "A": pA, "B": pB, "C": pC},
                   "sheet": sh, "verdict": None}, f, ensure_ascii=False, indent=2)
    print("\n联络表:", sh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
