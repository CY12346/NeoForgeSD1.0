# -*- coding: utf-8 -*-
"""兼容 shim：代码已移到 core/image/comfy_client.py。

import 时转出真模块的全部公开名；直接执行时转给真模块当 __main__ 跑。
"""
_TARGET = "core.image.comfy_client"

if __name__ == "__main__":  # pragma: no cover
    from runpy import run_module

    run_module(_TARGET, run_name="__main__", alter_sys=True)
else:
    from importlib import import_module

    _m = import_module(_TARGET)
    globals().update({k: v for k, v in vars(_m).items() if not k.startswith("__")})
