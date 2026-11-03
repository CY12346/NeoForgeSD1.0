# -*- coding: utf-8 -*-
"""热度榜：插件和 UI 本体的受欢迎程度。

数据源只有 GitHub（api.github.com 在本机实测通，0.5 s/次；github.com 2026-11-04 也恢复了）。
魔搭那条线 openapi 没有 stars 字段，只有 downloads —— 两个来源不混着排，免得「4 万下载」
跟「16 万 star」放一起比。

缓存 work/popularity.json，7 天内不重复拉。拉不到就用内置的实测值兜底（catalog.ITEMS 里）。
"""
import json
import os
import time
import urllib.error
import urllib.request

API = "https://api.github.com/repos/"
UA = {"User-Agent": "NeoForgeSD/1.3 (+local launcher)"}
CACHE = "work/popularity.json"
TTL = 7 * 86400

# 目录条目 id -> GitHub 仓库（只有插件和 UI 有）
REPOS = {
    "ext-adetailer": "Bing-su/adetailer",
    "ext-prompt-all-in-one": "Physton/sd-webui-prompt-all-in-one",
    "ext-tagcomplete": "DominikDoom/a1111-sd-webui-tagcomplete",
    "ext-zh-hans": "hanamizuki-ai/stable-diffusion-webui-localization-zh_Hans",
    "ext-controlnet": "Mikubill/sd-webui-controlnet",
    "ui-forge": "lllyasviel/stable-diffusion-webui-forge",
    "ui-comfyui": "Comfy-Org/ComfyUI",
    "ui-a1111": "AUTOMATIC1111/stable-diffusion-webui",
}


def _path(base_dir):
    return os.path.join(base_dir, CACHE.replace("/", os.sep))


def load(base_dir):
    """{id: 记录}，没有就空 dict。不联网。"""
    p = _path(base_dir)
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("items") or {}
    except (OSError, ValueError):
        return {}


def stale(base_dir):
    p = _path(base_dir)
    if not os.path.isfile(p):
        return True
    try:
        with open(p, encoding="utf-8") as f:
            ts = json.load(f).get("updated") or 0
        return (time.time() - ts) > TTL
    except (OSError, ValueError):
        return True


def fetch(ids=None, timeout=10):
    """真去 GitHub 拉。返回 {id: {stars, forks, pushed, name}}，单个失败不影响其它。"""
    out = {}
    for iid, repo in REPOS.items():
        if ids and iid not in ids:
            continue
        try:
            req = urllib.request.Request(API + repo, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8"))
            out[iid] = {
                "stars": d.get("stargazers_count") or 0,
                "forks": d.get("forks_count") or 0,
                "pushed": (d.get("pushed_at") or "")[:10],
                "repo": repo,
                "archived": bool(d.get("archived")),
            }
        except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError):
            continue
        time.sleep(0.2)                       # 未鉴权 60 次/小时，别贴着打
    return out


def refresh(base_dir, log=None):
    """拉一次并落盘，跟已有缓存合并（拉失败的保留旧值）。"""
    log = log or (lambda _m: None)
    old = load(base_dir)
    got = fetch()
    if not got:
        log("热度榜：GitHub 一个都没拉到，继续用本地已有的")
        return old
    old.update(got)
    p = _path(base_dir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"updated": time.time(), "items": old}, f, ensure_ascii=False, indent=1)
    log("热度榜：更新 %d 项（%s）" % (len(got), p))
    return old


def by_id(base_dir):
    """给 catalog 用：{id: stars}。先读缓存，缓存里没有的用内置兜底。"""
    cache = load(base_dir)
    return {k: (v or {}).get("stars") or 0 for k, v in cache.items()}


def human(n):
    """165236 -> 16.5 万。界面上列宽有限。"""
    n = int(n or 0)
    if n >= 10000:
        return "%.1f万" % (n / 10000.0)
    if n >= 1000:
        return "%.1fk" % (n / 1000.0)
    return str(n)
