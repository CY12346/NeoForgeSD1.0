# -*- coding: utf-8 -*-
"""
IP-Adapter 对症复验（FaceID 撞到 "InsightFace: No face detected." 之后）
  T1  ip-adapter-plus-face_sd15 + CLIP-ViT-H   ← 不依赖人脸检测，动漫脸直接可用
  T2  ip-adapter-faceid-plusv2_sd15 + InsightFace + 半写实参考图（墨幽2.5D出的）
      ← 验证"真人脸检测器能不能吃 2.5D 脸"
各自与基线（无单元, seed777）做像素差分 + 取新增日志 + 存图待 vision
"""
import base64
import json
import os
import sys

from PIL import Image, ImageChops

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from core.sdclient import SD                        # noqa: E402

LOG = "D:/aicg/_logs/sd-launch.log"
OUT = os.path.join(HERE, "spike-out", "ipa2")
os.makedirs(OUT, exist_ok=True)
BASE = os.path.join(HERE, "spike-out", "cn", "N0_无单元.png")
REF_ANIME = os.path.join(HERE, "spike-out", "ipa", "cast.png")
REF_25D = os.path.join(HERE, "spike-out", "style", "墨幽2.5D_24步.png")

PROMPT = ("1boy, solo, young man, short messy black hair, scar on cheek, white shirt, "
          "standing in a flower shop, medium shot, anime, cel shading, flat color, "
          "bold clean outlines, masterpiece, best quality")
NEG = "lowres, bad anatomy, worst quality, watermark, text, extra digits, realistic, 3d, photo"


def dataurl(p):
    return "data:image/png;base64," + base64.b64encode(open(p, "rb").read()).decode()


def run(sd, tag, module, model, ref, weight=0.9):
    size = os.path.getsize(LOG)
    payload = {"prompt": PROMPT, "negative_prompt": NEG, "width": 512, "height": 896,
               "steps": 20, "cfg_scale": 7.0, "sampler_name": "DPM++ 2M",
               "scheduler": "karras", "seed": 777, "save_images": False,
               "alwayson_scripts": {"controlnet": {"args": [{
                   "enabled": True, "image": dataurl(ref), "module": module,
                   "model": model, "weight": weight, "resize_mode": 1,
                   "low_vram": False, "processor_res": 512,
                   "guidance_start": 0.0, "guidance_end": 1.0,
                   "pixel_perfect": False, "control_mode": 0}]}}}
    res = sd.post("/sdapi/v1/txt2img", payload)
    p = sd.save(sd._decode(res)[0], os.path.join(OUT, f"{tag}.png"))
    new = open(LOG, "rb").read()[size:].decode("utf-8", "replace")
    hits = [l.strip() for l in new.splitlines()
            if any(k in l.lower() for k in ("ipadapter", "ip-adapter", "insightface",
                                            "no face", "clip", "error running"))]
    d = ImageChops.difference(Image.open(BASE).convert("RGB"),
                             Image.open(p).convert("RGB"))
    h = d.histogram()
    n = Image.open(BASE).width * Image.open(BASE).height
    rms = (sum(i * i * h[i] for i in range(256)) / (3 * n)) ** 0.5
    return {"file": p, "rms": round(rms, 2), "log": hits[-8:]}


def main():
    sd = SD()
    out = {}
    for tag, module, model, ref in [
        ("T1_plusface_anime", "CLIP-ViT-H (IPAdapter)", "ip-adapter-plus-face_sd15", REF_ANIME),
        ("T2_faceid_2p5D", "InsightFace+CLIP-H (IPAdapter)", "ip-adapter-faceid-plusv2_sd15", REF_25D),
    ]:
        print(f"\n[{tag}] module={module} model={model}")
        try:
            r = run(sd, tag, module, model, ref)
            print(f"   rms={r['rms']}  {'✅ 生效' if r['rms'] > 1 else '❌ 未生效'}")
            for l in r["log"]:
                print("     ", l[:160])
        except Exception as e:
            r = {"error": str(e)[:400]}
            print("   失败:", r["error"][:300])
        out[tag] = r

    json.dump(out, open(os.path.join(HERE, "spike-out", "ipa2.json"), "w",
                        encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\n已写 spike-out/ipa2.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
