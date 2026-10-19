# -*- coding: utf-8 -*-
"""图像主线（出图）。comfy_client = ComfyUI 客户端，sdclient = NeoForge/Forge WebUI 客户端。

上层只认 `generate(shot) -> [png bytes]`（见 manhua.py 的 Backend 抽象），换引擎不动业务代码。
"""
__all__ = ["comfy_client", "sdclient"]
