#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CP1-spike：竖屏 768x1344 出图速度三档对比（同 prompt / 同尺寸 / 各 3 张取中位）
改用 core/sdclient.py 内核 —— 切模型/LoRA 的坑都在核心里处理过了。

  python spike1_speed.py
输出： spike-out/speed.json + speed-out/*.png
"""
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))          # manhua_engine 根
from core.sdclient import SD                        # noqa: E402

OUT = os.path.join(HERE, "spike-out")
os.makedirs(OUT, exist_ok=True)

W, H = 768, 1344          # 红果同规格竖屏
SEED = 12345
N = 3

POS = ("1boy, solo, young man, short black messy hair, scar on cheek, "
       "white shirt, standing in a flower shop, flowers, warm sunlight, "
       "anime screencap, cel shading, bold outlines, high saturation, "
       "masterpiece, best quality, highly detailed")
NEG = ("lowres, bad anatomy, bad hands, worst quality, low quality, watermark, "
       "text, signature, jpeg artifacts, extra digits, realistic, 3d, photo")


def main():
    sd = SD()
    if not sd.alive():
        print("!! SD 未就绪（http://127.0.0.1:7860）")
        return 1

    ckpt = sd.pick_checkpoint("llustrious", "SDXL")
    if not ckpt:
        print("!! 没找到二次元/SDXL 底模")
        return 1
    print(f"== 底模: {ckpt}")
    base = sd.set_checkpoint(ckpt)
    print(f"== 生效: {base}（读回 {sd.current_checkpoint()}）\n")

    n_light = sd.pick_lora("sdxl_lightning_4step")
    n_hyper = sd.pick_lora("hyper-sdxl", "4step")
    print("== LoRA 选用:")
    print("   lightning:", n_light)
    print("   hyper    :", n_hyper)
    print("   (可用 Lightning:", [x for x in sd.loras() if "lightning" in x.lower()], ")")
    print()

    configs = [
        ("1_无加速_28步", 28, 5.0, "DPM++ 2M", None),
        ("2_Lightning_4步", 4, 2.0, "DPM++ SDE",
         [(n_light, 1.0)] if n_light else None),
        ("3_Hyper_4步", 4, 1.5, "DPM++ SDE",
         [(n_hyper, 1.0)] if n_hyper else None),
    ]

    results = {}
    for name, steps, cfg, sampler, loras in configs:
        if loras and not loras[0][0]:
            print(f"[{name}] 跳过（没找到该 LoRA）\n")
            continue
        times = []
        for k in range(N):
            t0 = time.time()
            try:
                pngs = sd.txt2img(POS, NEG, w=W, h=H, steps=steps, cfg=cfg,
                                  sampler=sampler, seed=SEED + k, loras=loras)
            except Exception as e:
                print(f"[{name}] 第{k+1}张 失败: {str(e)[:200]}")
                break
            dt = time.time() - t0
            times.append(dt)
            for i, png in enumerate(pngs):
                sd.save(png, os.path.join(OUT, f"{name}_{k+1}_{i}.png"))
            print(f"[{name}] 第{k+1}/{N}张  {dt:6.2f}s   {W}x{H}")
        if times:
            results[name] = {"times": [round(t, 2) for t in times],
                             "median": round(statistics.median(times), 2),
                             "steps": steps, "cfg": cfg, "sampler": sampler,
                             "loras": loras or []}
            print(f"  -> 中位 {results[name]['median']}s\n")

    with open(os.path.join(OUT, "speed.json"), "w", encoding="utf-8") as f:
        json.dump({"checkpoint": base, "size": f"{W}x{H}", "seed": SEED,
                   "prompt": POS, "negative": NEG, "results": results},
                  f, ensure_ascii=False, indent=2)
    print("== 汇总（竖屏 768x1344）==")
    for k, v in results.items():
        print(f"  {k:18s} 中位 {v['median']:6.2f}s  ({v['steps']}步 cfg{v['cfg']} {v['sampler']})")
    print("已写:", os.path.join(OUT, "speed.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
