#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CP1-ComfyUI：Anima 竖屏实测（含两次随机种子测试）
工作流来源：circlestone-labs/Anima 的 example.png 内嵌官方 API 图（已提取为 _anima_prompt.json）

输出：spike-out/comfy/ 下图片 + comfy_anima.json（速度/显存/随机性测试结果）
"""
import json
import os
import random
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from core.comfy_client import Comfy                   # noqa: E402

OUT = os.path.join(HERE, "spike-out", "comfy")
os.makedirs(OUT, exist_ok=True)
TEMPLATE = "D:/AIGC/comfyui/_anima_prompt.json"

UNET = "anima-turbo-v1.1.safetensors"
POS = ("masterpiece, best quality, score_7, safe. An anime boy with short messy "
       "black hair and purple eyes, wearing a white shirt, standing behind the "
       "counter of a small flower shop, buckets of colorful flowers around him, "
       "warm afternoon sunlight through the window, upper body, looking slightly away.")
NEG = "worst quality, low quality, score_1, score_2, score_3, blurry, jpeg artifacts, sepia"


def build(w=768, h=1344, steps=30, cfg=4.0, sampler="er_sde", scheduler="simple",
          seed=None, unet=UNET):
    wf = json.load(open(TEMPLATE, encoding="utf-8"))
    wf["44"]["inputs"]["unet_name"] = unet
    wf["11"]["inputs"]["text"] = POS
    wf["12"]["inputs"]["text"] = NEG
    wf["19"]["inputs"].update({"steps": steps, "cfg": cfg, "sampler_name": sampler,
                               "scheduler": scheduler,
                               "seed": seed if seed is not None else random.randint(0, 2**31)})
    wf["28"]["inputs"].update({"width": w, "height": h, "batch_size": 1})
    # PreviewImage → SaveImage（API 下要落盘才取得到）
    wf["1"] = {"inputs": {"images": ["8", 0], "filename_prefix": "anima"},
               "class_type": "SaveImage", "_meta": {"title": "Save Image"}}
    return wf


def vram(c):
    try:
        d = (c.stats().get("devices") or [{}])[0]
        return {"vram_free_gb": round((d.get("vram_free") or 0) / 2**30, 2),
                "vram_total_gb": round((d.get("vram_total") or 0) / 2**30, 2),
                "torch_free_gb": round((d.get("torch_vram_free") or 0) / 2**30, 2)}
    except Exception as e:
        return {"error": str(e)[:80]}


def run(c, tag, **kw):
    print(f"\n[{tag}] {kw}", flush=True)
    wf = build(**kw)
    before = vram(c)
    t0 = time.time()
    try:
        imgs = c.txt2img(wf, timeout=3600)
    except Exception as e:
        dt = time.time() - t0
        print(f"   ✗ 失败（{dt:.1f}s）: {str(e)[:400]}", flush=True)
        return {"tag": tag, "error": str(e)[:400], "seconds": round(dt, 1), **kw}
    dt = time.time() - t0
    paths = []
    for i, b in enumerate(imgs):
        p = c.save(b, os.path.join(OUT, f"{tag}_{i}.png"))
        paths.append(p)
    after = vram(c)
    r = {"tag": tag, "seconds": round(dt, 1), "images": paths,
         "vram_before": before, "vram_after": after, **kw}
    print(f"   ✓ {dt:.1f}s  → {paths[0] if paths else '(无图)'}", flush=True)
    print(f"     显存 前{before.get('vram_free_gb')}G/后{after.get('vram_free_gb')}G "
          f"(总{after.get('vram_total_gb')}G)", flush=True)
    return r


def main():
    c = Comfy()
    if not c.alive():
        print("!! ComfyUI 未就绪（127.0.0.1:8188）")
        return 1
    print("ComfyUI 就绪，显存:", vram(c))

    results = []

    # ① 官方基准参数（turbo 底模也先按官方拍一遍，确认图能出来）
    results.append(run(c, "A_官方参数_768x1344", w=768, h=1344, steps=30, cfg=4.0))

    # ② turbo 少步档（速度优先）
    results.append(run(c, "B_turbo8步_768x1344", w=768, h=1344, steps=8, cfg=1.5))

    # ③④ 两次随机种子测试（用户要求的"随机性测试"：同 prompt、随机 seed，各一次）
    for i in (1, 2):
        s = random.randint(1, 2**31 - 1)
        results.append(run(c, f"R{i}_随机种子测试_seed{s}", w=768, h=1344, steps=8, cfg=1.5, seed=s))

    with open(os.path.join(HERE, "spike-out", "comfy_anima.json"), "w", encoding="utf-8") as f:
        json.dump({"unet": UNET, "prompt": POS, "negative": NEG, "results": results},
                  f, ensure_ascii=False, indent=2)

    print("\n== 汇总 ==")
    ok = [r for r in results if "error" not in r]
    for r in results:
        st = f"{r.get('seconds')}s" if "error" not in r else "失败"
        print(f"  {r['tag']:34s} {st:>8s}  {r.get('error','')[:60]}")
    print(f"\n成功 {len(ok)}/{len(results)}；已写 spike-out/comfy_anima.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
