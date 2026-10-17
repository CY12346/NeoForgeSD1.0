#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
checkpoint_split_fp8.py — 把 SDXL 全家桶 checkpoint 拆成 ComfyUI 分件，并出 fp8 UNet 降显存
==================================================================================
为什么拆：4GB 显存上跑 SDXL 竖屏，权重显存是最大头。UNETLoader 的
weight_dtype=fp8_e4m3fn 能把 UNet 权重显存砍一半（~2.5G → ~1.25G），
把省下的空间让给 768x1344 的激活值，配合 DynamicVRAM 不糊图。

用法（用 ComfyUI 的 venv python，自带 torch2.8+safetensors）：
  D:\\AIGC\\comfyui\\venv\\Scripts\\python.exe checkpoint_split_fp8.py <checkpoint.safetensors> <comfyui根目录>

产出（文件名 = checkpoint 文件名去扩展名）：
  models/diffusion_models/<stem>-fp8.safetensors   UNet fp8_e4m3fn（出图用这个）
  models/diffusion_models/<stem>-fp16.safetensors  UNet fp16（对比基准/保底）
  models/text_encoders/<stem>_clip_l.safetensors   CLIP-L（fp16）
  models/text_encoders/<stem>_clip_g.safetensors   ViT-bigG（fp16）
  models/vae/<stem>_vae.safetensors                VAE（fp16）

SDXL checkpoint 张量前缀（实测依据）：
  model.diffusion_model.   = UNet
  conditioner.embedders.0. = CLIP-L（DualCLIPLoader 的 clip_name1）
  conditioner.embedders.1. = ViT-bigG（clip_name2）
  first_stage_model.       = VAE
"""
import os
import sys
import time

import torch
from safetensors import safe_open
from safetensors.torch import save_file

FP8 = torch.float8_e4m3fn


def split(ckpt_path: str, root: str):
    stem = os.path.splitext(os.path.basename(ckpt_path))[0]
    dm_dir = os.path.join(root, "models", "diffusion_models")
    te_dir = os.path.join(root, "models", "text_encoders")
    vae_dir = os.path.join(root, "models", "vae")
    for d in (dm_dir, te_dir, vae_dir):
        os.makedirs(d, exist_ok=True)

    parts = {"unet": {}, "clip_l": {}, "clip_g": {}, "vae": {}}
    print(f"读取 {ckpt_path} ...", flush=True)
    t0 = time.time()
    with safe_open(ckpt_path, framework="pt", device="cpu") as f:
        for k in f.keys():
            if k.startswith("model.diffusion_model."):
                parts["unet"][k[len("model.diffusion_model."):]] = f.get_tensor(k)
            elif k.startswith("conditioner.embedders.0."):
                parts["clip_l"][k[len("conditioner.embedders.0."):]] = f.get_tensor(k)
            elif k.startswith("conditioner.embedders.1."):
                parts["clip_g"][k[len("conditioner.embedders.1."):]] = f.get_tensor(k)
            elif k.startswith("first_stage_model."):
                parts["vae"][k[len("first_stage_model."):]] = f.get_tensor(k)
    print(f"  读入完成 {time.time()-t0:.0f}s：unet {len(parts['unet'])} 键 / "
          f"clip_l {len(parts['clip_l'])} / clip_g {len(parts['clip_g'])} / vae {len(parts['vae'])}", flush=True)

    def cast(t, dtype):
        return t.to(dtype) if t.is_floating_point() else t

    def save(d, path, dtype):
        t0 = time.time()
        save_file({k: cast(v, dtype) for k, v in d.items()}, path)
        print(f"  ✓ {os.path.basename(path)}  {os.path.getsize(path)/1e6:.0f}MB  {time.time()-t0:.0f}s", flush=True)

    if not parts["unet"]:
        raise SystemExit("✗ 没找到 UNet 张量——这不是标准 SDXL checkpoint？")
    save(parts["unet"], os.path.join(dm_dir, f"{stem}-fp8.safetensors"), FP8)
    save(parts["unet"], os.path.join(dm_dir, f"{stem}-fp16.safetensors"), torch.float16)
    if parts["clip_l"]:
        save(parts["clip_l"], os.path.join(te_dir, f"{stem}_clip_l.safetensors"), torch.float16)
    if parts["clip_g"]:
        save(parts["clip_g"], os.path.join(te_dir, f"{stem}_clip_g.safetensors"), torch.float16)
    if parts["vae"]:
        save(parts["vae"], os.path.join(vae_dir, f"{stem}_vae.safetensors"), torch.float16)
    print("\n=== 拆分完成 ===")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    split(sys.argv[1], sys.argv[2])
