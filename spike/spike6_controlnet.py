#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
判定题：ControlNet 单元到底吃不吃？（同 seed A/B 差分法，不靠嘴）
  T1 openpose   ：有单元 vs 无单元，同 seed 同 prompt → 图不同 = 生效
  T2 IP-Adapter ：有单元 vs 无单元，同 seed 同 prompt → 图不同 = 生效
判定量：像素差（PIL ImageChops 的 rms）+ 日志痕迹。
两种 alwayson 键名都试（"controlnet" / "ControlNet"），因为 Forge 与扩展各有叫法。
"""
import base64
import json
import os
import sys

from PIL import Image, ImageChops

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from core.sdclient import SD                        # noqa: E402

OUT = os.path.join(HERE, "spike-out", "cn")
os.makedirs(OUT, exist_ok=True)
W, H = 512, 896
CKPT = "AWPainting"
REF = os.path.join(HERE, "spike-out", "ipa", "cast.png")   # 定妆图当参考

CARD = ("1boy, solo, young man, short messy black hair, a short faint scar on "
        "his cheek, pale skin, white shirt, purple eyes")
STYLE = ("anime, cel shading, flat color, bold clean outlines, high saturation, "
         "masterpiece, best quality")
PROMPT = f"{CARD}, standing in a flower shop, medium shot, {STYLE}"
NEG = ("lowres, bad anatomy, bad hands, worst quality, watermark, text, signature, "
       "extra digits, realistic, 3d, photo, blurry")
SEED = 4242


def dataurl(path):
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


def gen(sd, unit_key=None, unit=None, tag="x"):
    payload = {"prompt": PROMPT, "negative_prompt": NEG, "width": W, "height": H,
               "steps": 20, "cfg_scale": 7.0, "sampler_name": "DPM++ 2M",
               "scheduler": "karras", "seed": SEED, "save_images": False}
    if unit_key:
        payload["alwayson_scripts"] = {unit_key: {"args": [unit]}}
    res = sd.post("/sdapi/v1/txt2img", payload)
    p = sd.save(sd._decode(res)[0], os.path.join(OUT, f"{tag}.png"))
    return p


def diff(a, b):
    ia, ib = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
    d = ImageChops.difference(ia, ib)
    h = d.histogram()
    n = ia.width * ia.height
    rms = (sum(i * i * h[i] for i in range(256)) / (3 * n)) ** 0.5
    bbox = d.getbbox()
    return round(rms, 3), bbox


def main():
    sd = SD()
    if not sd.alive():
        print("!! SD 未就绪")
        return 1
    sd.set_checkpoint(sd.pick_checkpoint(CKPT))
    ref = dataurl(REF)   # 裸 base64 字符串 —— Forge 的 ControlNetUnit.from_dict 要的就是这个

    print("[基线] 无单元 ...")
    base = gen(sd, None, None, "T0_无单元")
    print("   ->", base)

    results = {}

    # T1 openpose
    unit = {"enabled": True, "image": ref, "module": "openpose",
            "model": "control_v11p_sd15_openpose", "weight": 1.0, "resize_mode": 1,
            "low_vram": False, "processor_res": 512, "threshold_a": 64,
            "threshold_b": 64, "guidance_start": 0.0, "guidance_end": 1.0,
            "pixel_perfect": True, "control_mode": 0}
    for key in ("controlnet", "ControlNet"):
        try:
            p = gen(sd, key, unit, f"T1_openpose_{key}")
            rms, bbox = diff(base, p)
            print(f"[T1 openpose/{key}] rms={rms} bbox={bbox}  {'✅生效' if rms > 1.0 else '❌疑似未生效'}")
            results[f"openpose_{key}"] = {"rms": rms, "bbox": str(bbox), "file": p}
        except Exception as e:
            print(f"[T1 openpose/{key}] 失败: {str(e)[:200]}")
            results[f"openpose_{key}"] = {"error": str(e)[:200]}

    # T2 IP-Adapter FaceID
    unit2 = {"enabled": True, "image": ref,
             "module": "InsightFace+CLIP-H (IPAdapter)",
             "model": "ip-adapter-faceid-plusv2_sd15", "weight": 1.0,
             "resize_mode": 1, "low_vram": False, "processor_res": 512,
             "guidance_start": 0.0, "guidance_end": 1.0,
             "pixel_perfect": False, "control_mode": 0}
    for key in ("controlnet", "ControlNet"):
        try:
            p = gen(sd, key, unit2, f"T2_ipadapter_{key}")
            rms, bbox = diff(base, p)
            print(f"[T2 ip-adapter/{key}] rms={rms} bbox={bbox}  {'✅生效' if rms > 1.0 else '❌疑似未生效'}")
            results[f"ipadapter_{key}"] = {"rms": rms, "bbox": str(bbox), "file": p}
        except Exception as e:
            print(f"[T2 ip-adapter/{key}] 失败: {str(e)[:200]}")
            results[f"ipadapter_{key}"] = {"error": str(e)[:200]}

    with open(os.path.join(HERE, "spike-out", "cn.json"), "w", encoding="utf-8") as f:
        json.dump({"seed": SEED, "prompt": PROMPT, "baseline": base,
                   "results": results}, f, ensure_ascii=False, indent=2)
    print("\n已写: spike-out/cn.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
