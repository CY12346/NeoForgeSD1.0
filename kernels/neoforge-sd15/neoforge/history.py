# -*- coding: utf-8 -*-
"""历史记录：work/history_image.json，最多 300 条，原子写（tmp + os.replace）。"""
import json
import threading

from . import config

_LOCK = threading.Lock()


def load():
    try:
        with open(config.HISTORY_FILE, encoding="utf-8") as f:
            items = json.load(f)
        return items if isinstance(items, list) else []
    except Exception:
        return []


def add(rec):
    with _LOCK:
        items = load()
        items.insert(0, rec)
        items = items[:300]
        tmp = config.HISTORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
        import os
        os.replace(tmp, config.HISTORY_FILE)


def recent(limit=24):
    """给 /api/history：截断 + 补上可点的 /files/ 地址。"""
    out = []
    for rec in load()[:max(1, min(300, int(limit or 24)))]:
        r = dict(rec)
        r["urls"] = ["/files/" + f for f in rec.get("files") or []]
        out.append(r)
    return out
