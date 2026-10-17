#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CP1-spike 之五：IP-Adapter FaceID 实跑（一致性最优解候选）
  底模 AWPainting(SD1.5) / 512x896 / 24步
  单元：module="InsightFace+CLIP-H (IPAdapter)" model="ip-adapter-faceid-plusv2_sd15"
        image = 定妆图 → 自由文生图（换场景换 seed），看脸是否被锁住
对比：定妆图 | A 纯文生图 | B IP-FaceID 场景1 | C IP-FaceID 场景2（换景换 seed）
输出：spike-out/ipa/ + 联络表 ipa-sheet.jpg + ipa.json
"""
import base64
import json
import os
import sys
import time

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from core.sdclient import SD                        # noqa: E402

OUT = os.path.join(HERE, "spike-out", "ipa")
os.makedirs(OUT, exist_ok=True)
W, H = 512, 896
CKPT = "AWPainting"
IPA_MODULE = "InsightFace+CLIP-H (IPAdapter)"
IPA_MODEL = "ip-adapter-faceid-plusv2_sd15"

CARD = ("1boy, solo, young man, short messy black hair, a short faint scar on "
        "his cheek, pale skin, white shirt, purple eyes")
STYLE = ("anime, cel shading, flat color, bold clean outlines, high saturation, "
         "masterpiece, best quality")
NEG = ("lowres, bad anatomy, bad hands, worst quality, watermark, text, signature, "
       "extra digits, realistic, 3d, photo, blurry")
PORTRAIT = f"{CARD}, portrait, upper body, front view, looking at viewer, plain warm grey background, {STYLE}"
SCENE1 = f"{CARD}, standing behind a wooden counter in a small flower shop, buckets of flowers, warm afternoon sunlight, medium shot, {STYLE}"
SCENE2 = f"{CARD}, walking on a rainy street at night, neon reflections, close-up, looking away, {STYLE}"


def dataurl(path):
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


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


def gen(sd, prompt, seed, ref, weight=0.75):
    payload = {
        "prompt": prompt, "negative_prompt": NEG,
        "width": W, "height": H, "steps": 24, "cfg_scale": 7.0,
        "sampler_name": "DPM++ 2M", "scheduler": "karras",
        "seed": seed, "save_images": False,
    }
    if ref:
        payload["alwayson_scripts"] = {"controlnet": {"args": [{
            "enabled": True,
            "image": {"image": dataurl(ref)},
            "module": IPA_MODULE,
            "model": IPA_MODEL,
            "weight": weight,
            "resize_mode": 1,
            "pixel_perfect": False,
            "guidance_start": 0.0,
            "guidance_end": 1.0,
            "control_mode": 0,
            "processor_res": 512,
        }]}}
    t0 = time.time()
    res = sd.post("/sdapi/v1/txt2img", payload)
    dt = time.time() - t0
    imgs = sd._decode(res)
    return imgs[0], dt


def main():
    sd = SD()
    if not sd.alive():
        print("!! SD 未就绪")
        return 1
    base = sd.set_checkpoint(sd.pick_checkpoint(CKPT))
    print("底模:", base)

    print("\n[1/4] 定妆图 ...")
    cast_png, t = gen(sd, PORTRAIT, 777, None)
    cast = sd.save(cast_png, os.path.join(OUT, "cast.png"))
    print(f"   {t:.1f}s -> {cast}")

    print("[2/4] A 纯文生图（无参考）...")
    a_png, t = gen(sd, SCENE1, 902, None)
    pA = sd.save(a_png, os.path.join(OUT, "A_纯文生图.png"))
    print(f"   {t:.1f}s")

    print("[3/4] B IP-FaceID 锁脸 → 场景1 ...")
    try:
        b_png, t = gen(sd, SCENE1, 902, cast, weight=0.75)
        pB = sd.save(b_png, os.path.join(OUT, "B_ipfaceid_场景1.png"))
        print(f"   {t:.1f}s")
    except Exception as e:
        print("   失败:", str(e)[:300])
        pB = None

    print("[4/4] C IP-FaceID 锁脸 → 场景2（换景换seed）...")
    try:
        c_png, t = gen(sd, SCENE2, 313, cast, weight=0.75)
        pC = sd.save(c_png, os.path.join(OUT, "C_ipfaceid_场景2.png"))
        print(f"   {t:.1f}s")
    except Exception as e:
        print("   失败:", str(e)[:300])
        pC = None

    made = [p for p in (cast, pA, pB, pC) if p]
    labels = ["定妆图", "A 纯文生图", "B IP-FaceID 场景1", "C IP-FaceID 场景2"][:len(made)]
    sh = sheet(made, labels, os.path.join(HERE, "spike-out", "ipa-sheet.jpg"))
    with open(os.path.join(HERE, "spike-out", "ipa.json"), "w", encoding="utf-8") as f:
        json.dump({"checkpoint": base, "module": IPA_MODULE, "model": IPA_MODEL,
                   "weight": 0.75, "files": {"cast": cast, "A": pA, "B": pB, "C": pC},
                   "sheet": sh}, f, ensure_ascii=False, indent=2)
    print("\n联络表:", sh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
