#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
manhua_engine · SD 客户端内核（可复用层）
=========================================
把本机 NeoForge 后端（Forge Classic 兼容 WebUI）的实测脾气固化在这里，上层脚本一律用它，不再各自踩坑。

实测坑（2026-10-16，本机 NeoForge WebUI（Forge Classic）+ RTX3050 4GB）：
 1. `sd_model_checkpoint` 只认 **不带目录、不带扩展名** 的基名：
      "Illustrious-XL-v0.1"                    -> 200 ✅
      "SDXL\\Illustrious-XL-v0.1.safetensors"   -> 422 not found
      "Illustrious-XL-v0.1.safetensors"        -> 500 且静默不切换（最坑）
 2. **已经是当前模型时再切一次会 500**（RuntimeError: model '...safetensors [hash]' not found），
    但切换其实已成功 → 必须先读回、相同即跳过。
 3. 服务刚启动的极短时间内 POST 会返回 404（路由尚未注册齐）→ 需要重试。
 4. LoRA 用 `<lora:文件名不带扩展名:权重>` 写在正向 prompt 里。
 5. **采样器名不带 "Karras" 后缀**（这个 NeoForge WebUI 把调度器分开了）：
      "DPM++ 2M Karras" -> 404 Sampler not found  ← 一路"神秘 404"的真身
      "DPM++ 2M" + scheduler "karras" -> 正常
    所以出图前一律用 samplers()/schedulers() 的真实名单校验。
 6. **ControlNet / IP-Adapter 的 image 必须传「裸 base64 字符串」，不能传成字典**：
      "image": "data:image/png;base64,..."          ✅ 生效（实测 rms=61.6）
      "image": {"image": "data:image/png;base64..."} ❌ TypeError: '<' not supported
         between instances of 'str' and 'int' → unit 被静默 skipped
      依据：`sd_forge_controlnet/lib_controlnet/external_code.py` 的
      `ControlNetUnit.from_dict()` 只在 `isinstance(unit.image, str)` 时才解码。
    调用形如：
      "alwayson_scripts": {"controlnet": {"args": [{
          "enabled": True, "image": <裸base64串>, "module": "openpose",
          "model": "control_v11p_sd15_openpose", "weight": 1.0, "resize_mode": 1,
          "low_vram": False, "processor_res": 512, "threshold_a": 64, "threshold_b": 64,
          "guidance_start": 0.0, "guidance_end": 1.0, "pixel_perfect": True,
          "control_mode": 0}]}}
      取证方式（别靠猜）：比日志字节偏移后取新增行，看到
      `[ControlNet] - INFO - ControlNet Method openpose patched.` 才算生效；
      或同 seed 开/关单元做像素差分（rms>1 即生效）。
    注：首次用某模块会从 HuggingFace 下模型（openpose 注释器 ≈ 百 MB 级；
      IP-Adapter 的 CLIP 可达 2.35G），本机 ~3.8MB/s，要等。
"""
from __future__ import annotations

import base64
import io
import json
import os
import time
import urllib.error
import urllib.request


class SD:
    def __init__(self, api: str = "http://127.0.0.1:7860", retries: int = 4):
        self.api = api.rstrip("/")
        self.retries = retries

    # ---------- 低层 ----------
    def _call(self, path: str, payload=None, timeout: int = 1800, method=None):
        url = self.api + path
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        last = None
        for attempt in range(self.retries):
            req = urllib.request.Request(
                url, data=data,
                headers={"Content-Type": "application/json"},
                method=method or ("POST" if data is not None else "GET"))
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:      # 坑 3：启动瞬时的 404
                last = e
                if e.code in (404, 502, 503) and attempt < self.retries - 1:
                    time.sleep(3)
                    continue
                detail = e.read()[:300].decode("utf-8", "replace")
                raise RuntimeError(f"{path} -> HTTP {e.code}: {detail}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                last = e
                if attempt < self.retries - 1:
                    time.sleep(3)
                    continue
                raise RuntimeError(f"{path} 连不上：{e}") from e
        raise RuntimeError(f"{path} 重试 {self.retries} 次仍失败：{last}")

    def get(self, path, timeout=120):
        return self._call(path, None, timeout, method="GET")

    def post(self, path, payload, timeout=1800):
        return self._call(path, payload, timeout, method="POST")

    # ---------- 探活 / 模型 ----------
    def alive(self, timeout=8) -> bool:
        try:
            self.get("/sdapi/v1/options", timeout=timeout)
            return True
        except Exception:
            return False

    def checkpoints(self):
        return [m["title"] for m in self.get("/sdapi/v1/sd-models")]

    def loras(self):
        try:
            return [l["name"] if isinstance(l, dict) else str(l)
                    for l in self.get("/sdapi/v1/loras")]
        except Exception:
            return []

    def current_checkpoint(self) -> str:
        try:
            return self.get("/sdapi/v1/options").get("sd_model_checkpoint") or ""
        except Exception:
            return ""

    @staticmethod
    def _base_name(title: str) -> str:
        """'SDXL\\Illustrious-XL-v0.1.safetensors [abc]' -> 'Illustrious-XL-v0.1'"""
        b = os.path.basename(title.split(" [")[0])
        for ext in (".safetensors", ".ckpt"):
            if b.lower().endswith(ext):
                b = b[: -len(ext)]
        return b

    def set_checkpoint(self, title: str, wait_s: int = 180) -> str:
        """切模型（自动处理坑 1/2/3）。返回实际生效的基名。"""
        base = self._base_name(title)
        if base in self.current_checkpoint():        # 坑 2：同模型重切会 500，直接跳过
            return base
        try:
            self.post("/sdapi/v1/options", {"sd_model_checkpoint": base}, timeout=wait_s)
        except RuntimeError as e:
            print(f"   (切模型报错，按读回判定：{str(e)[:100]})")
        deadline = time.time() + wait_s
        while time.time() < deadline:
            cur = self.current_checkpoint()
            if base in cur:
                return base
            time.sleep(2)
        raise RuntimeError(f"切模型失败：想要 {base}，当前 {self.current_checkpoint()!r}")

    def samplers(self):
        try:
            return [s["name"] for s in self.get("/sdapi/v1/samplers")]
        except Exception:
            return []

    def schedulers(self):
        try:
            return [(s.get("name") or s.get("label")) for s in self.get("/sdapi/v1/schedulers")]
        except Exception:
            return []

    def pick_sampler(self, *needles: str) -> str:
        """按子串挑采样器；挑不到退到 Euler a。坑 5：名字里没有 Karras。"""
        have = self.samplers()
        for n in needles:
            for s in have:
                if n.lower() in s.lower():
                    return s
        return "Euler a" if "Euler a" in have else (have[0] if have else "Euler a")

    def check_sampler(self, sampler: str, scheduler: str | None = None) -> str:
        have = self.samplers()
        if have and sampler not in have:
            near = [s for s in have if sampler.split()[0].lower() in s.lower()] or have
            raise RuntimeError(
                f"采样器 {sampler!r} 不存在（坑5：名字不带 Karras；调度器要用 scheduler 参数）。"
                f"可选：{have}；相近：{near}")
        if scheduler:
            sch = self.schedulers()
            if sch and scheduler not in sch:
                raise RuntimeError(f"调度器 {scheduler!r} 不存在。可选：{sch}")
        return sampler

    def pick_checkpoint(self, *needles: str) -> str | None:
        """按子串优先级挑底模全名"""
        cps = self.checkpoints()
        for n in needles:
            for c in cps:
                if n.lower() in c.lower():
                    return c
        return None

    def pick_lora(self, *needles: str) -> str | None:
        """挑 LoRA 名：**所有子串都命中**才算（AND）。
        注意别写成 OR —— 那会静默选错 LoRA（例：想要 Hyper-SD15-4steps 却拿到 1step）。"""
        all_l = self.loras()
        for l in all_l:
            low = l.lower()
            if all(n.lower() in low for n in needles):
                return self._base_name(l)
        # 退而求其次：只用第一个子串
        for l in all_l:
            if needles[0].lower() in l.lower():
                return self._base_name(l)
        return None

    # ---------- 出图 ----------
    @staticmethod
    def _decode(res):
        out = []
        for b64 in res.get("images", []):
            out.append(base64.b64decode(b64.split(",", 1)[-1]))
        return out

    def txt2img(self, prompt, negative="", w=768, h=1344, steps=28, cfg=5.0,
                sampler="DPM++ 2M", scheduler="karras", seed=-1, loras=None):
        """返回 PNG bytes 列表（loras=[(名字,权重), ...]）"""
        self.check_sampler(sampler, scheduler)
        p = prompt
        if loras:
            p += " " + " ".join(f"<lora:{self._base_name(n)}:{wt}>" for n, wt in loras)
        res = self.post("/sdapi/v1/txt2img", {
            "prompt": p, "negative_prompt": negative,
            "width": w, "height": h, "steps": steps, "cfg_scale": cfg,
            "sampler_name": sampler, "scheduler": scheduler,
            "seed": seed, "save_images": False,
        })
        return self._decode(res)

    def img2img(self, init_png: bytes, prompt, negative="", denoise=0.45,
                steps=28, cfg=5.0, sampler="DPM++ 2M", scheduler="karras",
                seed=-1, loras=None):
        self.check_sampler(sampler, scheduler)
        p = prompt
        if loras:
            p += " " + " ".join(f"<lora:{self._base_name(n)}:{wt}>" for n, wt in loras)
        b64 = base64.b64encode(init_png).decode("ascii")
        res = self.post("/sdapi/v1/img2img", {
            "init_images": [b64], "prompt": p, "negative_prompt": negative,
            "denoising_strength": denoise, "steps": steps, "cfg_scale": cfg,
            "sampler_name": sampler, "scheduler": scheduler,
            "seed": seed, "save_images": False,
        })
        return self._decode(res)

    def save(self, png_bytes: bytes, path: str) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "wb") as f:
            f.write(png_bytes)
        return os.path.abspath(path)


if __name__ == "__main__":
    sd = SD()
    print("alive:", sd.alive())
    print("current:", sd.current_checkpoint())
    print("checkpoints:", sd.checkpoints())
