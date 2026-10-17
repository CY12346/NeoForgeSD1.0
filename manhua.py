#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
manhua.py — 出图调度器（可复用层之三）
======================================
把「镜头表」变成「一批落盘的图 + 一份 manifest」，一条命令出片。

设计原则（对应 comfy_client.py 里的承诺）：
  · 上层只认 generate(shot) -> [png bytes] 这一个接口
  · 底层是 ComfyUI 还是 NeoForge 由 --backend 决定，换引擎不动业务代码
  · 每个镜头独立计时 + 记录 seed + 落盘命名 {id}.png，失败不中断整批

用法：
  python manhua.py --shots shots.json --backend comfy --out out_dir [--limit 5] [--dry-run]

镜头表 shots.json 格式：
  {
    "backend": {                                    # 可选：后端默认参数
      "comfy": {"workflow": ".../_anima_prompt.json",
                "nodes": {"unet":"44","clip":"45","vae":"15",
                          "positive":"11","negative":"12","sampler":"19","latent":"28"},
                "unet":"anima-turbo-v1.1.safetensors",
                "clip":"qwen_3_06b_base.safetensors",
                "vae":"qwen_image_vae.safetensors"},
      "neoforge": {"checkpoint": "Illustrious-XL-v0.1"}
    },
    "defaults": {"width":768, "height":1344, "steps":8, "cfg":1.5,
                 "sampler":"er_sde", "scheduler":"simple", "seed":-1},
    "shots": [
      {"id":"s01", "prompt":"anime boy, flower shop counter, ..."},
      {"id":"s02", "prompt":"anime girl, ...", "steps":12, "seed":123}
    ]
  }

环境变量：
  COMFY_URL    ComfyUI 地址（默认 http://127.0.0.1:8188）
  SD_API       NeoForge WebUI 地址（默认 http://127.0.0.1:7860）
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "core"))

from comfy_client import Comfy  # noqa: E402
from sdclient import SD        # noqa: E402

DEFAULT_NODES = {
    "unet": "44", "clip": "45", "vae": "15",
    "positive": "11", "negative": "12", "sampler": "19", "latent": "28",
}


# --------------------------------------------------------------------------- #
# 后端抽象
# --------------------------------------------------------------------------- #
class Backend:
    name = "abstract"

    def alive(self) -> bool:
        raise NotImplementedError

    def generate(self, shot: dict) -> list[bytes]:
        raise NotImplementedError


class ComfyBackend(Backend):
    """ComfyUI 后端：加载 workflow 模板，按 node id 打点改值。"""

    name = "comfy"

    def __init__(self, url: str, cfg: dict):
        self.client = Comfy(url)
        self.workflow = self._load(cfg.get("workflow"))
        self.nodes = {**DEFAULT_NODES, **(cfg.get("nodes") or {})}
        self.unet = cfg.get("unet")
        self.clip = cfg.get("clip")
        # SDXL 双 CLIP（DualCLIPLoader：clip_name1=CLIP-L, clip_name2=ViT-bigG）
        self.clip_l = cfg.get("clip_l")
        self.clip_g = cfg.get("clip_g")
        self.vae = cfg.get("vae")

    @staticmethod
    def _load(wf):
        if wf is None:
            raise SystemExit("Comfy 后端需要 backend.comfy.workflow 指向一个 API 格式 workflow JSON")
        if isinstance(wf, dict):
            return wf
        if not os.path.exists(wf):
            raise SystemExit(f"workflow 不存在：{wf}")
        return json.load(open(wf, encoding="utf-8"))

    def alive(self) -> bool:
        return self.client.alive()

    def generate(self, shot: dict) -> list[bytes]:
        import copy
        wf = copy.deepcopy(self.workflow)
        p = self.nodes
        if self.unet and p["unet"] in wf:
            wf[p["unet"]]["inputs"]["unet_name"] = self.unet
        # 单 CLIP（DiT 类，如 Anima）与双 CLIP（SDXL 类）都支持
        clip_node = wf.get(p["clip"]) or {}
        clip_inputs = clip_node.get("inputs") or {}
        if "clip_name" in clip_inputs and self.clip:
            clip_inputs["clip_name"] = self.clip
        elif "clip_name1" in clip_inputs:
            if self.clip_l:
                clip_inputs["clip_name1"] = self.clip_l
            if self.clip_g:
                clip_inputs["clip_name2"] = self.clip_g
        if self.vae and p["vae"] in wf:
            wf[p["vae"]]["inputs"]["vae_name"] = self.vae
        if p["positive"] in wf:
            wf[p["positive"]]["inputs"]["text"] = shot["prompt"]
        if p["negative"] in wf:
            wf[p["negative"]]["inputs"]["text"] = shot.get("negative", "")
        if p["sampler"] in wf:
            wf[p["sampler"]]["inputs"].update({
                "steps": shot.get("steps"), "cfg": shot.get("cfg"),
                "sampler_name": shot.get("sampler"), "scheduler": shot.get("scheduler"),
                "seed": shot.get("seed"),
            })
        if p["latent"] in wf:
            wf[p["latent"]]["inputs"].update({
                "width": shot.get("width"), "height": shot.get("height"), "batch_size": 1,
            })
        # API 模式下必须落盘才取得到图：把第一个 PreviewImage 换成 SaveImage
        for nid, node in wf.items():
            if node.get("class_type") == "PreviewImage":
                wf[nid] = {"inputs": {"images": node["inputs"]["images"], "filename_prefix": "manhua"},
                           "class_type": "SaveImage", "_meta": {"title": "Save Image"}}
                break
        return self.client.txt2img(wf)


class NeoForgeBackend(Backend):
    """NeoForge WebUI 后端：走 /sdapi/v1/txt2img，自动处理切模型/采样器校验。"""

    name = "neoforge"

    def __init__(self, api: str, cfg: dict):
        self.client = SD(api)
        self.checkpoint = cfg.get("checkpoint")

    def alive(self) -> bool:
        return self.client.alive()

    def generate(self, shot: dict) -> list[bytes]:
        if self.checkpoint:
            self.client.set_checkpoint(self.checkpoint)
        pngs = self.client.txt2img(
            prompt=shot["prompt"], negative=shot.get("negative", ""),
            w=shot.get("width"), h=shot.get("height"),
            steps=shot.get("steps"), cfg=shot.get("cfg"),
            sampler=shot.get("sampler") or "DPM++ 2M",
            scheduler=shot.get("scheduler") or "karras",
            seed=shot.get("seed"),
        )
        return pngs


# --------------------------------------------------------------------------- #
# 镜头表 / 运行器
# --------------------------------------------------------------------------- #
def _pick_seed(shot):
    s = shot.get("seed")
    if s in (None, -1):
        return random.randint(1, 2**31 - 1)
    return int(s)


def load_shots(path: str) -> dict:
    if not os.path.exists(path):
        raise SystemExit(f"镜头表不存在：{path}")
    return json.load(open(path, encoding="utf-8"))


def run(shots: dict, backend: Backend, out_dir: str, limit: int = 0, dry: bool = False):
    os.makedirs(out_dir, exist_ok=True)
    defaults = shots.get("defaults") or {}
    items = shots.get("shots") or []
    if limit > 0:
        items = items[:limit]

    manifest = []
    ok = fail = 0
    t_total = time.time()
    for shot in items:
        sid = shot.get("id") or f"s{len(manifest)+1:02d}"
        # 合并默认值，缺省 seed 用随机
        for k, v in defaults.items():
            shot.setdefault(k, v)
        shot["seed"] = _pick_seed(shot)
        t0 = time.time()
        try:
            pngs = backend.generate(shot)
            paths = []
            for i, b in enumerate(pngs):
                p = os.path.join(out_dir, f"{sid}.png" if len(pngs) == 1 else f"{sid}_{i:02d}.png")
                if not dry:
                    with open(p, "wb") as f:
                        f.write(b)
                paths.append(p)
            secs = time.time() - t0
            ok += 1
            rec = {"id": sid, "ok": True, "seconds": round(secs, 1),
                   "seed": shot["seed"], "paths": paths,
                   "width": shot.get("width"), "height": shot.get("height"),
                   "steps": shot.get("steps"), "cfg": shot.get("cfg"),
                   "sampler": shot.get("sampler")}
            manifest.append(rec)
            print(f"  ✓ {sid}  {secs:5.1f}s  seed={shot['seed']}  -> {os.path.basename(paths[0]) if paths else '(无图)'}", flush=True)
        except Exception as e:
            fail += 1
            secs = time.time() - t0
            rec = {"id": sid, "ok": False, "seconds": round(secs, 1), "error": str(e)[:400]}
            manifest.append(rec)
            print(f"  ✗ {sid}  {secs:5.1f}s  {str(e)[:200]}", flush=True)

    total = time.time() - t_total
    if not dry:
        with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump({"backend": backend.name, "total_seconds": round(total, 1),
                       "ok": ok, "fail": fail, "shots": manifest},
                      f, ensure_ascii=False, indent=2)
    print(f"\n== 完成 {ok} 成功 / {fail} 失败，总耗时 {total:.1f}s = {total/60:.1f}min ==")
    print(f"   平均 {total/len(items):.1f}s/镜" if items else "")
    return manifest


def main():
    ap = argparse.ArgumentParser(description="出图调度器：镜头表 -> 批量出图")
    ap.add_argument("--shots", required=True, help="镜头表 JSON 路径")
    ap.add_argument("--backend", choices=["comfy", "neoforge"], default="comfy")
    ap.add_argument("--out", default="out", help="输出目录")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 镜（0=全部）")
    ap.add_argument("--dry-run", action="store_true", help="不落盘，只打印")
    args = ap.parse_args()

    shots = load_shots(args.shots)
    bcfg = (shots.get("backend") or {}).get(args.backend) or {}
    if args.backend == "comfy":
        backend = ComfyBackend(os.environ.get("COMFY_URL", "http://127.0.0.1:8188"), bcfg)
    else:
        backend = NeoForgeBackend(os.environ.get("SD_API", "http://127.0.0.1:7860"), bcfg)

    if not backend.alive():
        tip = ("启动 ComfyUI（双击 D:\\AIGC\\comfyui\\启动ComfyUI.cmd）" if args.backend == "comfy"
               else "启动 NeoForge WebUI（sd_start 或双击启动SD-API.bat）")
        raise SystemExit(f"{args.backend} 后端未就绪。{tip}")

    run(shots, backend, args.out, args.limit, args.dry_run)


if __name__ == "__main__":
    main()
