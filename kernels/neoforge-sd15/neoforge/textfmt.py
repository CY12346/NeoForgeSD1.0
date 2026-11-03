# -*- coding: utf-8 -*-
"""提示词层：中⇄英 / 补全 / 词库 / 翻译。词典靠 _vendor/i18n_tags（本机 SD 扩展的 CSV）。

词典坏了不该拖垮出图：这里把 import 失败收成「不可用 + 原因」，上层据此返回 503 + 人话。
"""
import time

ERR = ""
try:
    from ._vendor import i18n_tags as _lib
except Exception as _e:                      # 词典坏了也不该拖垮出图
    _lib = None
    ERR = "%s: %s" % (type(_e).__name__, str(_e)[:200])


def available():
    return _lib is not None


def error():
    return ERR


def stats():
    return _lib.stats()


def complete(q, limit=15):
    return _lib.complete(q, limit)


def promptlib():
    return _lib.promptlib()


def translate(text, direction="zh2en", web=True):
    """返回 dict + 本次耗时。方向不认识就当 zh2en。"""
    t0 = time.time()
    if direction == "en2zh":
        r = _lib.en2zh(text)
    else:
        r = _lib.zh2en(text, web=web)
    r["elapsed"] = round(time.time() - t0, 2)
    r["dir"] = direction
    return r
