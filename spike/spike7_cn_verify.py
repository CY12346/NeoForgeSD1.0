# -*- coding: utf-8 -*-
"""单发验证：openpose 单元（裸 base64 字符串格式）是否真的生效 —— 取新增日志为证"""
import base64
import json
import os
import sys

from PIL import Image, ImageChops

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from core.sdclient import SD                        # noqa: E402

LOG = "D:/aicg/_logs/sd-launch.log"
OUT = os.path.join(HERE, "spike-out", "cn")
REF = os.path.join(HERE, "spike-out", "ipa", "cast.png")
PROMPT = ("1boy, solo, young man, short messy black hair, scar on cheek, white shirt, "
          "standing in a flower shop, medium shot, anime, cel shading, flat color, "
          "bold clean outlines, masterpiece, best quality")
NEG = "lowres, bad anatomy, worst quality, watermark, text, extra digits, realistic, 3d, photo"


def dataurl(p):
    with open(p, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


def run(sd, tag, unit=None, key="controlnet"):
    payload = {"prompt": PROMPT, "negative_prompt": NEG, "width": 512, "height": 896,
               "steps": 20, "cfg_scale": 7.0, "sampler_name": "DPM++ 2M",
               "scheduler": "karras", "seed": 777, "save_images": False}
    if unit:
        payload["alwayson_scripts"] = {key: {"args": [unit]}}
    res = sd.post("/sdapi/v1/txt2img", payload)
    return sd.save(sd._decode(res)[0], os.path.join(OUT, f"{tag}.png"))


def main():
    sd = SD()
    ref = dataurl(REF)

    size0 = os.path.getsize(LOG)
    print("[A] 无单元 ...")
    a = run(sd, "N0_无单元")
    new = open(LOG, "rb").read()[size0:].decode("utf-8", "replace")

    size1 = os.path.getsize(LOG)
    print("[B] openpose 单元（裸 base64 字符串）...")
    unit = {"enabled": True, "image": ref, "module": "openpose",
            "model": "control_v11p_sd15_openpose", "weight": 1.0, "resize_mode": 1,
            "low_vram": False, "processor_res": 512, "threshold_a": 64,
            "threshold_b": 64, "guidance_start": 0.0, "guidance_end": 1.0,
            "pixel_perfect": True, "control_mode": 0}
    b = run(sd, "N1_openpose", unit)
    new2 = open(LOG, "rb").read()[size1:].decode("utf-8", "replace")

    d = ImageChops.difference(Image.open(a).convert("RGB"), Image.open(b).convert("RGB"))
    h = d.histogram()
    n = Image.open(a).width * Image.open(a).height
    rms = (sum(i * i * h[i] for i in range(256)) / (3 * n)) ** 0.5
    print(f"\n>>> 差分 rms={rms:.3f}  bbox={d.getbbox()}  {'✅ 生效（图不同）' if rms > 1 else '❌ 未生效（图相同）'}")

    print("\n--- B 步新增日志里 controlnet 相关行 ---")
    hits = [l for l in new2.splitlines()
            if any(k in l.lower() for k in ("controlnet", "preprocessor", "unit", "openpose"))]
    for l in hits[-25:]:
        print("   ", l)
    if not hits:
        print("    (无)")

    with open(os.path.join(HERE, "spike-out", "cn-verify.json"), "w", encoding="utf-8") as f:
        json.dump({"rms": rms, "a": a, "b": b, "log_hits": hits[-25:]}, f,
                  ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
