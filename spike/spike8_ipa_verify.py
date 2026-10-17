# -*- coding: utf-8 -*-
"""IP-Adapter FaceID 单发验证（同 seed 777 与基线对比 + 日志取证）"""
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
BASE = os.path.join(OUT, "N0_无单元.png")
PROMPT = ("1boy, solo, young man, short messy black hair, scar on cheek, white shirt, "
          "standing in a flower shop, medium shot, anime, cel shading, flat color, "
          "bold clean outlines, masterpiece, best quality")
NEG = "lowres, bad anatomy, worst quality, watermark, text, extra digits, realistic, 3d, photo"


def main():
    sd = SD()
    ref = "data:image/png;base64," + base64.b64encode(open(REF, "rb").read()).decode()

    size = os.path.getsize(LOG)
    payload = {"prompt": PROMPT, "negative_prompt": NEG, "width": 512, "height": 896,
               "steps": 20, "cfg_scale": 7.0, "sampler_name": "DPM++ 2M",
               "scheduler": "karras", "seed": 777, "save_images": False,
               "alwayson_scripts": {"controlnet": {"args": [{
                   "enabled": True, "image": ref,
                   "module": "InsightFace+CLIP-H (IPAdapter)",
                   "model": "ip-adapter-faceid-plusv2_sd15", "weight": 1.0,
                   "resize_mode": 1, "low_vram": False, "processor_res": 512,
                   "guidance_start": 0.0, "guidance_end": 1.0,
                   "pixel_perfect": False, "control_mode": 0}]}}}
    print("[IP-Adapter FaceID] 出图中 ...")
    res = sd.post("/sdapi/v1/txt2img", payload)
    p = sd.save(sd._decode(res)[0], os.path.join(OUT, "N2_ipadapter.png"))
    new = open(LOG, "rb").read()[size:].decode("utf-8", "replace")

    d = ImageChops.difference(Image.open(BASE).convert("RGB"),
                             Image.open(p).convert("RGB"))
    h = d.histogram()
    n = Image.open(BASE).width * Image.open(BASE).height
    rms = (sum(i * i * h[i] for i in range(256)) / (3 * n)) ** 0.5
    print(f"\n>>> vs 基线(同seed) rms={rms:.3f}  {'✅ 生效' if rms > 1 else '❌ 未生效'}")

    hits = [l for l in new.splitlines()
            if any(k in l.lower() for k in ("ip-adapter", "ipadapter", "insightface",
                                            "faceid", "clip", "controlnet", "download"))]
    print("\n--- 新增日志（相关性过滤）---")
    for l in hits[-25:]:
        print("   ", l)
    if not hits:
        print("    (无)")

    json.dump({"rms": rms, "file": p, "log_hits": hits[-25:]},
              open(os.path.join(HERE, "spike-out", "ipa-verify.json"), "w",
                   encoding="utf-8"), ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
