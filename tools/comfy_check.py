#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ComfyUI 启动自检：探活 + 确认 Dynamic VRAM 是否生效 + 列出可用模型/采样器"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from core.comfy_client import Comfy                   # noqa: E402


def main():
    c = Comfy()
    print("=== 探活 ===")
    if not c.alive():
        print("  ✗ ComfyUI 未就绪（http://127.0.0.1:8188）")
        return 1
    print("  ✓ 已就绪")

    s = c.stats()
    dev = (s.get("devices") or [{}])[0]
    print(f"\n=== 设备 ===")
    print(f"  {dev.get('name')}")
    print(f"  显存 总 {round((dev.get('vram_total') or 0)/2**30,2)}G / "
          f"空闲 {round((dev.get('vram_free') or 0)/2**30,2)}G")
    print(f"  torch 空闲 {round((dev.get('torch_vram_free') or 0)/2**30,2)}G")
    print(f"\n=== 系统 ===")
    print("  " + json.dumps(s.get("system") or {}, ensure_ascii=False))

    # Dynamic VRAM 判定：cli_args 里 --lowvram 的帮助文字说它"在 dynamic vram 启用时无作用"，
    # 这里看 /system_stats 之外最直接的证据是启动日志；这里给出提示词。
    print("\n=== 启动日志里应出现（判定 Dynamic VRAM 生效）===")
    print("  'DynamicVRAM' / 'dynamic vram' 相关字样；或 --lowvram 无警告生效")

    print("\n=== 模型文件 ===")
    for sub in ("diffusion_models", "text_encoders", "vae"):
        d = os.path.join(r"D:\AIGC\comfyui\models", sub)
        if os.path.isdir(d):
            for fn in sorted(os.listdir(d)):
                p = os.path.join(d, fn)
                if os.path.isfile(p) and os.path.getsize(p) > 1_000_000:
                    print(f"  {sub:16s} {fn:48s} {os.path.getsize(p)/2**30:.2f}G")

    print("\n=== 队列 ===", c.queue_state())
    return 0


if __name__ == "__main__":
    sys.exit(main())
