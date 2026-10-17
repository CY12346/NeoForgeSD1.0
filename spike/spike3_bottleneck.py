#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CP1-spike 之三：固定开销从哪来 —— 三条路各测一遍（各 2 张取均值）
  ① SDXL 640x1120 + Hyper 4步      ：分辨率是否线性影响（1.03MP -> 0.72MP）
  ② SD1.5二次元 512x896 + 24步      ：换底模（参数量 1/3）
  ③ SD1.5二次元 512x896 + HyperSD15 4步 ：换底模 + 加速
结论落 spike-out/speed2.json
"""
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from core.sdclient import SD                        # noqa: E402

OUT = os.path.join(HERE, "spike-out")
os.makedirs(OUT, exist_ok=True)

POS = ("1boy, solo, young man, short black messy hair, scar on cheek, white shirt, "
       "flower shop, flowers, warm sunlight, anime screencap, cel shading, "
       "bold outlines, high saturation, masterpiece, best quality, highly detailed")
NEG = ("lowres, bad anatomy, bad hands, worst quality, watermark, text, signature, "
       "extra digits, realistic, 3d, photo")
N = 2


def run(sd, label, w, h, steps, cfg, sampler, loras, ckpt):
    sd.set_checkpoint(ckpt)
    times = []
    for k in range(N):
        t0 = time.time()
        pngs = sd.txt2img(POS, NEG, w=w, h=h, steps=steps, cfg=cfg,
                          sampler=sampler, seed=500 + k, loras=loras)
        dt = time.time() - t0
        times.append(dt)
        for i, p in enumerate(pngs):
            sd.save(p, os.path.join(OUT, f"{label}_{k+1}_{i}.png"))
        print(f"[{label}] 第{k+1}/{N}张 {dt:6.2f}s  {w}x{h}")
    med = round(statistics.median(times), 2)
    print(f"  -> {label} 中位 {med}s\n")
    return {"label": label, "median": med, "times": [round(t, 2) for t in times],
            "size": f"{w}x{h}", "steps": steps, "cfg": cfg, "sampler": sampler,
            "ckpt": sd._base_name(ckpt),
            "loras": [[sd._base_name(n), wt] for n, wt in (loras or [])],
            "sec_per_mp": round(med / (w * h / 1e6), 1)}


def main():
    sd = SD()
    if not sd.alive():
        print("!! SD 未就绪")
        return 1

    sdxl = sd.pick_checkpoint("llustrious", "SDXL")
    # SD1.5 二次元：优先国产 2.5D 漫改风
    sd15 = sd.pick_checkpoint("墨幽2.5D", "AWPainting", "Dark Sushi", "ReVAnimated")
    print("SDXL :", sdxl)
    print("SD1.5:", sd15)
    if not sdxl or not sd15:
        print("!! 底模没找齐")
        return 1

    l_sdxl = [(sd.pick_lora("hyper-sdxl", "4step") or sd.pick_lora("sdxl_lightning_4step"), 1.0)]
    l_sd15 = [(sd.pick_lora("hyper-sd15", "4steps"), 1.0)]
    print("LoRA  SDXL:", l_sdxl, " SD1.5:", l_sd15, "\n")

    out = []
    out.append(run(sd, "S1_SDXL_640x1120_hyper4", 640, 1120, 4, 1.5,
                   "DPM++ SDE", l_sdxl, sdxl))
    out.append(run(sd, "S2_SD15_512x896_24步", 512, 896, 24, 7.0,
                   "DPM++ 2M", None, sd15))
    out.append(run(sd, "S3_SD15_512x896_hyper4", 512, 896, 4, 1.5,
                   "DPM++ SDE", l_sd15, sd15))

    with open(os.path.join(OUT, "speed2.json"), "w", encoding="utf-8") as f:
        json.dump({"prompt": POS, "negative": NEG, "results": out},
                  f, ensure_ascii=False, indent=2)
    print("== 汇总（含 每秒每百万像素 sec/MP，可比刻度）==")
    for r in out:
        print(f"  {r['label']:26s} {r['median']:6.2f}s  {r['size']:9s} "
              f"{r['steps']:2d}步  {r['sec_per_mp']:5.1f}s/MP  lora={[x[0] for x in r['loras']]}")
    print("已写:", os.path.join(OUT, "speed2.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
