# -*- coding: utf-8 -*-
"""参数装配层：页面来的 dict → SD API 的载荷。纯函数，不碰网络、不碰文件。"""
from . import config

PRESETS = {
    "512x512": (512, 512), "768x768": (768, 768),
    "512x896": (512, 896), "768x1344": (768, 1344),
    "832x1216": (832, 1216), "1216x832": (1216, 832),
}


def _apply_loras(prompt, loras):
    """把 {name, weight} 列表拼成 <lora:name:weight> —— WebUI 原本的写法，前端不用手打。"""
    tags = []
    for item in loras or []:
        name = (item.get("name") or "").strip() if isinstance(item, dict) else str(item).strip()
        if not name:
            continue
        try:
            w = float(item.get("weight", 0.8)) if isinstance(item, dict) else 0.8
        except Exception:
            w = 0.8
        w = max(0.0, min(2.0, w))
        tag = f"<lora:{name}:{w:g}>"
        if tag not in prompt:
            tags.append(tag)
    return (prompt + " " + " ".join(tags)).strip() if tags else prompt


def _common(a):
    w = int(a.get("width") or 512)
    h = int(a.get("height") or 512)
    n = max(1, min(8, int(a.get("n") or 1)))
    prompt = _apply_loras((a.get("prompt") or "").strip(), a.get("loras"))
    return {
        "prompt": prompt,
        "negative_prompt": (a.get("negative") or "").strip(),
        "steps": int(a.get("steps") or 28),
        "cfg_scale": float(a.get("cfg") or 7),
        "width": w, "height": h,
        "sampler_name": a.get("sampler") or "DPM++ 2M",
        "scheduler": a.get("scheduler") or "karras",
        "seed": int(a.get("seed") if a.get("seed") not in (None, "") else -1),
        "batch_size": 1, "n_iter": n,
        "save_images": False, "send_images": True,
        # 低端卡提速：s_min_uncond 跳过接近完成时的负面提示词分支（不改画面）；
        # 该设置只作用于本次请求，不污染 WebUI 的全局 config。
        "override_settings": dict(config._OVERRIDE),
    }


def _adetailer(a):
    """ADetailer 面部修复（老 SD 那套也在用 face_yolov8n.pt）—— 走 alwayson_scripts。"""
    ad = a.get("adetailer") or {}
    if not ad.get("enabled"):
        return None
    return {"adetailer": {"args": [True, False, {
        "ad_model": ad.get("model") or "face_yolov8n.pt",
        "ad_confidence": max(0.0, min(1.0, float(ad.get("confidence") or 0.3))),
        "ad_denoising_strength": max(0.0, min(1.0, float(ad.get("denoise") or 0.4))),
        "ad_mask_blur": 4,
        "ad_inpaint_only_masked": True,
        "ad_inpaint_only_masked_padding": 32,
        "ad_use_steps": False, "ad_steps": 28,
        "ad_use_cfg_scale": False, "ad_cfg_scale": 7.0,
        "ad_x_offset": 0, "ad_y_offset": 0,
    }]}}


def build_txt2img(a):
    p = _common(a)
    if not p["prompt"]:
        raise ValueError("提示词不能为空")
    ad = _adetailer(a)
    if ad:
        p["alwayson_scripts"] = ad
    hr = a.get("hires") or {}
    if hr.get("enabled"):
        # 高清修复：低分辨率先出图，再 ×scale 放大重绘一遍（WebUI 的 Hires.fix）
        p.update({
            "enable_hr": True,
            "hr_scale": max(1.0, min(4.0, float(hr.get("scale") or 1.5))),
            "hr_upscaler": hr.get("upscaler") or "Latent",
            "hr_second_pass_steps": int(hr.get("steps") or 0),
            "denoising_strength": max(0.0, min(1.0, float(hr.get("denoise") or 0.4))),
            "hr_resize_x": 0, "hr_resize_y": 0,
        })
    return p


def build_img2img(a):
    p = _common(a)
    src = (a.get("image_b64") or "").split(",")[-1]
    if not src:
        raise ValueError("缺少源图（上传一张，或用画布这张）")
    if (a.get("size_mode") or "keep") == "keep":
        # 不传宽高 = 用源图尺寸（WebUI 的「保持原尺寸」）
        p.pop("width", None)
        p.pop("height", None)
    p.update({"init_images": [src], "denoising_strength": float(a.get("denoise") or 0.45),
              "resize_mode": int(a.get("resize_mode") or 1), "include_init_images": False})
    mask = (a.get("mask_b64") or "").split(",")[-1]
    if mask:
        # 局部重绘：mask 白=重绘；inpainting_fill 1=原图底，2=latent noise
        p.update({
            "mask": mask,
            "mask_blur": int(a.get("mask_blur", 4)),
            "inpainting_fill": int(a.get("inpainting_fill", 1)),
            "inpaint_full_res": bool(a.get("inpaint_full_res", True)),
            "inpaint_full_res_padding": int(a.get("inpaint_full_res_padding", 32)),
            "inpainting_mask_invert": 0,
        })
    ad = _adetailer(a)
    if ad:
        p["alwayson_scripts"] = ad
    return p


def build_upscale(a):
    src = (a.get("image_b64") or "").split(",")[-1]
    if not src:
        raise ValueError("缺少源图（上传一张，或用画布这张）")
    return {"image": src,
            "upscaling_resize": float(a.get("scale") or 2),
            "upscaler_1": a.get("upscaler") or "4x-AnimeSharp",
            "upscaler_2": "None", "upscaler_2_visibility": 0,
            "gfpgan_visibility": max(0.0, min(1.0, float(a.get("gfpgan") or 0))),
            "codeformer_visibility": max(0.0, min(1.0, float(a.get("codeformer") or 0))),
            "codeformer_weight": 0.5}


BUILDERS = {"image": build_txt2img, "edit": build_img2img, "upscale": build_upscale}
