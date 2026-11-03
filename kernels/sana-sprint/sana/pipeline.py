# -*- coding: utf-8 -*-
"""一次出图的全部动作：文本编码（CPU）→ 扩散（GPU）→ 存图。

不碰端口、不碰 HTTP，方便单测和将来接别的入口。
"""
import os
import time

from . import config


def make(a):
    """请求参数 → 内部参数。缺省值照 SANA-Sprint 的官方推荐。"""
    w = int(a.get("width") or a.get("w") or 1024)
    h = int(a.get("height") or a.get("h") or w)
    steps = int(a.get("steps") or 2)
    if steps != 2:
        # SCM 蒸馏只支持 2 步；写死提示比让 diffusers 抛个看不懂的 ValueError 好
        raise ValueError("SANA-Sprint 是 2 步蒸馏模型，steps 只能是 2（收到 %d）" % steps)
    return {
        "prompt": (a.get("prompt") or "").strip(),
        "negative": (a.get("negative") or "").strip(),
        "width": max(256, min(2048, w)),
        "height": max(256, min(2048, h)),
        "steps": steps,
        "guidance": float(a.get("cfg") or a.get("guidance") or 4.5),
        "seed": int(a["seed"]) if str(a.get("seed", "")).strip() not in ("", "-1", "None")
                else -1,
    }


def generate(p, uid):
    """跑一张。返回 (文件路径, 秒数, 峰值显存 GiB)。"""
    import torch
    pipe = config.PIPE
    if pipe is None:
        config.warmup()
        pipe = config.PIPE

    t0 = time.time()
    seed = p["seed"] if p["seed"] >= 0 else int(torch.randint(0, 2**31 - 1, (1,)).item())
    gen = torch.Generator("cpu").manual_seed(seed)

    with torch.no_grad():
        embeds, mask = pipe.encode_prompt(
            p["prompt"], num_images_per_prompt=1, device=torch.device("cpu"),
            max_sequence_length=300)

    size = (p["width"], p["height"])
    latents = torch.randn(1, config.LATENT_CHANNELS,
                          size[1] // config.DOWNSAMPLE, size[0] // config.DOWNSAMPLE,
                          device="cpu", dtype=torch.float16, generator=gen).to("cuda")
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()

    with torch.no_grad():
        out = pipe(prompt_embeds=embeds.to("cuda"), prompt_attention_mask=mask.to("cuda"),
                   latents=latents, height=size[1], width=size[0],
                   num_inference_steps=p["steps"], guidance_scale=p["guidance"],
                   generator=gen, output_type="pil", use_resolution_binning=False)

    os.makedirs(config.WORK, exist_ok=True)
    path = os.path.join(config.WORK, "%s_00.png" % uid)
    out.images[0].save(path)
    peak = (torch.cuda.max_memory_allocated() / 2**30) if torch.cuda.is_available() else 0.0
    return path, round(time.time() - t0, 2), round(peak, 2), seed
