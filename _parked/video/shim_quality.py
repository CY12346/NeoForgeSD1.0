# -*- coding: utf-8 -*-
"""兼容 shim：代码已移到 core/video/quality.py。

`python core/quality.py out/wave.mp4` 仍然可用。
"""
_TARGET = "core.video.quality"

if __name__ == "__main__":  # pragma: no cover
    from runpy import run_module

    run_module(_TARGET, run_name="__main__", alter_sys=True)
else:
    from importlib import import_module

    _m = import_module(_TARGET)
    globals().update({k: v for k, v in vars(_m).items() if not k.startswith("__")})
