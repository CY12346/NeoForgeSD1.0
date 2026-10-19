# -*- coding: utf-8 -*-
"""兼容 shim：代码已移到 core/image/sdclient.py。"""
_TARGET = "core.image.sdclient"

if __name__ == "__main__":  # pragma: no cover
    from runpy import run_module

    run_module(_TARGET, run_name="__main__", alter_sys=True)
else:
    from importlib import import_module

    _m = import_module(_TARGET)
    globals().update({k: v for k, v in vars(_m).items() if not k.startswith("__")})
