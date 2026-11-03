# -*- coding: utf-8 -*-
"""在线模型源（ModelScope）：给启动器「获得大模型/小模型数据」的能力。

为什么是 ModelScope：本机实测（2026-11-03）
    civitai.com / huggingface.co  — 15 s 超时，不通
    api.github.com               — 通，但没有模型库
    modelscope.cn 详情 API        — 200，0.5 s
    modelscope.cn 文件树 API      — 200，带精确字节数
    modelscope.cn sitemap-models — 200，500 条模型 URL（免登录）
搜索接口（/dolphin/models 等）已下线或要求登录，所以「发现」靠 sitemap + 用户粘链接，
「取数据」靠详情 + 文件树，两个都真跑过。

条目 schema 与 core/catalog.py 一致，直接喂 downloader。
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

MIB = 1048576
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
API = "https://modelscope.cn/api/v1/models/"
SITEMAP = "https://www.modelscope.cn/sitemap-models.xml"
CACHE = "work/catalog-online.json"

WEIGHT_EXT = (".safetensors", ".ckpt", ".pth", ".pt", ".bin")

# 落点：类型 -> 目录（A1111/Forge 认的那几个标准目录）
DIRS = {
    "底模": "models/Stable-diffusion",
    "LoRA": "models/Lora",
    "VAE": "models/VAE",
    "ControlNet": "models/ControlNet",
    "放大": "models/ESRGAN",
    "修脸": "models/GFPGAN",
    "动画": "models/AnimateDiff",
}


class FetchError(Exception):
    pass


def _get(url, timeout=20, raw=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code}") from e
    except (urllib.error.URLError, OSError) as e:
        raise FetchError(str(e)) from e
    return body if raw else json.loads(body.decode("utf-8", "replace"))


# ---------------------------------------------------------------- 解析入口
def parse_ref(text):
    """把用户粘的东西解析成 'owner/name'：URL、'owner/name'、或者带 revision 的链接都行。"""
    if not text:
        return None
    t = text.strip()
    m = re.search(r"modelscope\.(?:cn|com)/models/([^/?#\s]+)/([^/?#\s]+)", t)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    m = re.match(r"^([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)$", t)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return None


def meta(repo):
    """仓库详情：名称 / 中文名 / tags / tasks / 下载量 / 许可 / 更新时间。"""
    d = _get(API + urllib.parse.quote(repo))
    if "Data" not in d:
        raise FetchError(d.get("Message") or "仓库不存在")
    x = d["Data"]
    return {
        "repo": repo,
        "name": x.get("Name") or repo.split("/")[-1],
        "cn": (x.get("ChineseName") or "").strip(),
        "tags": [t for t in (x.get("Tags") or []) if isinstance(t, str)],
        "tasks": [(t.get("ChineseName") or t.get("Name") or "") for t in (x.get("Tasks") or [])],
        "downloads": x.get("Downloads") or x.get("DownloadsCount") or 0,
        "license": x.get("License") or "",
        "updated": x.get("LastUpdatedTime") or 0,
        "size_bytes": x.get("Size") or x.get("FileSize") or 0,
    }


def files(repo, revision="master"):
    """文件树（递归）：[(path, size_bytes)]，只留 blob。"""
    url = (API + urllib.parse.quote(repo) + "/repo/files?Revision="
           + urllib.parse.quote(revision) + "&Recursive=true")
    d = _get(url)
    out = []
    for f in ((d.get("Data") or {}).get("Files") or []):
        if f.get("Type") != "blob":
            continue
        out.append((f.get("Path") or "", int(f.get("Size") or 0)))
    return out


def pick_weight(blobs):
    """挑该下哪个文件：根目录优先 → .safetensors 优先 → 有 emaonly 就它 → 最大的。

    这条顺序是照 SD1.5 / SDXL 两个仓库的真实文件树定的：
    SD1.5 里 v1-5-pruned.safetensors(7.3G) 比 emaonly(4.1G) 大，但 emaonly 才是 A1111 惯用的那个；
    SDXL 里最大的文件是 unet/ 子目录的分片，真底模是根目录的 sd_xl_base_1.0.safetensors。
    """
    cands = [b for b in blobs if b[0].lower().endswith(WEIGHT_EXT)]
    if not cands:
        return None
    top = [b for b in cands if "/" not in b[0]] or cands
    safe = [b for b in top if b[0].lower().endswith(".safetensors")] or top
    ema = [b for b in safe if "emaonly" in b[0].lower()]
    return max(ema or safe, key=lambda b: b[1])


def classify(m, fname, size_bytes, repo=""):
    """返回 (子类型, 目录)。子类型就是界面上「类型」列显示的字。

    统一走 type_from_tags：搜索结果和 hydrate 后的详情两条路给出的类型必须一致，
    否则同一行在下载前/后类型会跳。
    """
    nameish = f"{fname} {repo}".strip()
    tags = list(m.get("tags") or []) + [nameish]
    sub = type_from_tags(tags, nameish, size_bytes)
    if sub == "其他":
        fn = (fname or "").lower()
        if "vae" in fn and "novae" not in fn:
            sub = "VAE"
        elif "esrgan" in fn or "upscal" in fn:
            sub = "放大"
        elif size_bytes >= 4 * 1024 * MIB:
            sub = _base_model_era(nameish.lower())
    return sub, DIRS.get(sub.split(" ")[0], "models/Stable-diffusion")



def to_item(repo, m=None, blobs=None):
    """仓库 → catalog 条目。拉不到权重就返回 None（只有代码/配置的仓库不算模型）。"""
    m = m or meta(repo)
    blobs = blobs if blobs is not None else files(repo)
    picked = pick_weight(blobs)
    if not picked:
        return None
    path, size = picked
    kind, folder = classify(m, path, size, repo)
    fname = os.path.basename(path)
    # diffusers 系仓库的权重名千篇一律（pytorch_lora_weights / diffusion_pytorch_model），
    # 直接用会互相覆盖，改成仓库名
    stem, ext = os.path.splitext(fname)
    if stem.lower() in ("pytorch_lora_weights", "diffusion_pytorch_model", "model", "weights"):
        fname = repo.split("/")[-1] + ext
    url = ("https://modelscope.cn/api/v1/models/" + urllib.parse.quote(repo) + "/repo"
           "?Revision=master&FilePath=" + urllib.parse.quote(path))
    title = m.get("cn") or m.get("name") or repo
    desc = (f"ModelScope · {repo} · 下载 {m.get('downloads', 0):,} · 许可 {m.get('license') or '未标'}"
            f" · 文件 {fname}")
    return {
        "id": "ms-" + repo.replace("/", "_"),
        "kind": "model-big" if kind.startswith("底模") else "model-small",
        "subtype": kind,
        "name": title,
        "desc": desc,
        "size": round(size / MIB, 1),
        "urls": [url],
        "file": f"{folder}/{fname}",
        "source": "modelscope",
        "repo": repo,
    }


# ---------------------------------------------------------------- 发现（openapi 搜索）
SEARCH_API = "https://modelscope.cn/openapi/v1/models"

# openapi 里 tags 的写法是 "custom_tag:Checkpoint" / "library:lora" 这种，取冒号后面
TAG_KINDS = (
    ("zh-cn", "语音"), ("asr", "语音"), ("speech", "语音"),
    ("controlnet", "ControlNet"), ("t2i-adapter", "ControlNet"),
    ("upscaler", "放大"), ("esrgan", "放大"),
    ("vae", "VAE"), ("lora", "LoRA"), ("lycoris", "LoRA"),
    ("checkpoint", "底模"), ("text-to-image-synthesis", "底模"),
)


def _tag_variants(tags):
    return " ".join(t.split(":", 1)[1].lower() if ":" in t else t.lower() for t in tags)


def _split_tags(tags):
    """分成「作者/平台的模型类型标注」和「文件格式库标注」。

    魔搭的 tags 混着两种：custom_tag:Checkpoint 是模型类型（权威），
    library:lora 只是「能用 lora 库加载」——所以类型判定必须先看 custom_tag，
    否则 SD1.5 底模会被 library:lora 带跑（实测 MusePublic 的几个底模就是这样）。
    """
    custom, lib = [], []
    for t in tags or []:
        k, _, v = t.partition(":")
        v = (v or t).lower()
        if k == "library":
            lib.append(v)
        else:
            custom.append(v)
    return " ".join(custom), " ".join(lib)


def type_from_tags(tags, name="", size_bytes=0):
    """从 openapi 的 tags 读出「类型」—— 这是 PCL 那种「名称 + 类型」的类型的来源。"""
    custom, lib = _split_tags(tags)
    nm = (name or "").lower()
    allt = custom + " " + lib + " " + nm

    if ("vae" in custom or "vae" in nm) and "novae" not in nm:
        return "VAE"
    if "controlnet" in allt or "control_net" in allt or "t2i-adapter" in allt:
        return "ControlNet"
    if "upscaler" in allt or "esrgan" in allt:
        return "放大"
    if "gfpgan" in allt or "codeformer" in allt:
        return "修脸"
    # 魔搭社区常用 _ckpt_ / _lora_ 标在仓库名里（MusePublic/50_ckpt_SD_1_5 就是 ChilloutMix 底模）
    if re.search(r"(^|[-_])ckpt([-_]|$)", nm) or "checkpoint" in nm:
        return _base_model_era(allt)
    if "lora" in nm or "lycoris" in nm:
        return "LoRA"
    if "checkpoint" in custom:
        return _base_model_era(allt)
    # 体积兜底：LoRA 很少超 4 GiB，超过的多半是底模（魔搭的 library:lora 标注不可靠）
    if size_bytes >= 4 * 1024 * MIB and "lora" not in custom:
        return _base_model_era(allt)
    if "lora" in custom or "lycoris" in custom or "lora" in lib:
        return "LoRA"
    if "text-to-image" in custom or "diffusion" in custom:
        return _base_model_era(allt)
    return "其他"


def _base_model_era(allt):
    # 魔搭的仓库名把代数写成 _SD_1_5 / _SD_XL，统一成空格再比
    t = " " + allt.replace("_", " ").replace("-", " ").replace(".", " ") + " "
    if "flux" in t:
        return "底模 · FLUX"
    if "qwen image" in t:
        return "底模 · Qwen-Image"
    if "sdxl" in t or "sd xl" in t or "stable diffusion xl" in t:
        return "底模 · SDXL"
    if "sd 1 5" in t or "sd1 5" in t or "stable diffusion v1" in t:
        return "底模 · SD1.5"
    return "底模"





def _from_search(m):
    """openapi 一条搜索结果 → 清单候选（体积/类型/下载量都齐了，不用再问详情）。"""
    repo = m.get("id") or ""
    tags = m.get("tags") or []
    name = m.get("display_name") or repo.split("/")[-1]
    size_b = int(m.get("file_size") or 0)
    sub = type_from_tags(tags, f"{name} {repo}", size_b)
    folder = DIRS.get(sub.split(" ")[0], "models/Stable-diffusion")
    desc = (m.get("description") or "").strip().replace("\n", " ")
    if len(desc) > 120:
        desc = desc[:120] + "…"
    tail = (f"魔搭 · {repo} · 下载 {m.get('downloads') or 0:,}"
            f" · 许可 {m.get('license') or '未标'}")
    return {
        "id": "ms-" + repo.replace("/", "_"),
        "kind": "model-big" if sub.startswith("底模") else "model-small",
        "subtype": sub,
        "name": name,
        "desc": (desc + " · " + tail) if desc else tail,
        "size": round(size_b / MIB, 1) if size_b else None,
        "urls": [],                      # 下载前再问文件树（hydrate）
        "file": f"{folder}/{repo.split('/')[-1]}.safetensors",
        "source": "modelscope",
        "repo": repo,
        "tags": tags,                    # 留着原始标签，本地重算类型时不用再联网
    }


def search(keyword="", page=1, page_size=20, sort="downloads"):
    """魔搭公开搜索：分页参数是 page_number / page_size / search / sort（snake_case）。

    实测（2026-11-03）：search=lora 返回真 LoRA，search=sd1.5 返回 SD1.5 单文件仓库。
    """
    q = {"page_number": page, "page_size": page_size}
    if keyword:
        q["search"] = keyword
    if sort:
        q["sort"] = sort
    d = _get(SEARCH_API + "?" + urllib.parse.urlencode(q))
    models = ((d.get("data") or {}).get("models")) or []
    return [_from_search(m) for m in models]


def hydrate(item):
    """在线候选只有在真要下载时才需要知道「具体下哪个文件」——这里补全 urls / 落点 / 精确体积。

    候选条目是搜索来的（廉价的元数据），hydrate 会多打两个请求（详情 + 文件树），
    所以只在下单时做，不在列清单时做。
    """
    if not item.get("repo") or item.get("urls"):
        return item
    try:
        fresh = to_item(item["repo"])
    except FetchError:
        return item
    if not fresh:
        return item
    out = dict(item)
    out["urls"] = fresh["urls"]
    out["file"] = fresh["file"]
    out["size"] = fresh["size"]
    out["subtype"] = fresh["subtype"]
    out["kind"] = fresh["kind"]
    return out


def sitemap_repos(keywords=None, limit=200):
    """从免登录的 sitemap 拿模型仓库名；keywords 给了就按子串过滤（不区分大小写）。"""
    body = _get(SITEMAP, timeout=30, raw=True).decode("utf-8", "replace")
    try:
        root = ET.fromstring(body)
        locs = [e.text or "" for e in root.iter() if e.tag.endswith("loc")]
    except ET.ParseError:
        locs = re.findall(r"<loc>([^<]+)</loc>", body)
    repos = []
    for loc in locs:
        m = re.search(r"/models/([^/]+)/([^/?#]+)", loc)
        if not m:
            continue
        repo = f"{m.group(1)}/{m.group(2)}"
        if keywords and not any(k.lower() in repo.lower() for k in keywords):
            continue
        repos.append(repo)
        if limit and len(repos) >= limit:
            break
    return repos


# ---------------------------------------------------------------- 缓存
def cache_path(base_dir):
    return os.path.join(base_dir, CACHE.replace("/", os.sep))


def load_cache(base_dir):
    p = cache_path(base_dir)
    if not os.path.isfile(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("items") if isinstance(data, dict) else data
        return [it for it in (items or []) if isinstance(it, dict) and it.get("id")]
    except (OSError, ValueError):
        return []


def save_cache(base_dir, items):
    p = cache_path(base_dir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"updated": _now(), "items": items}, f, ensure_ascii=False, indent=1)
    return p


def _now():
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S")


def refresh(base_dir, repos, log=None, limit=None):
    """批量把仓库解析成条目并写进缓存（合并，不覆盖已解析过的）。"""
    log = log or (lambda _m: None)
    have = {it["id"]: it for it in load_cache(base_dir)}
    ok = fail = skip = 0
    for i, repo in enumerate(repos, 1):
        if limit and ok + skip >= limit:
            break
        iid = "ms-" + repo.replace("/", "_")
        if iid in have:
            skip += 1
            continue
        try:
            item = to_item(repo)
        except FetchError as e:
            fail += 1
            log(f"  ✗ {repo}：{e}")
            continue
        if not item:
            fail += 1
            log(f"  ✗ {repo}：没有可下的权重文件")
            continue
        have[iid] = item
        ok += 1
        log(f"  ✓ {item['subtype']} · {item['name']}（{item['size']:.0f} MiB）")
    items = list(have.values())
    save_cache(base_dir, items)
    return {"ok": ok, "fail": fail, "skip": skip, "total": len(items)}


# ---------------------------------------------------------------- 自检
if __name__ == "__main__":
    import sys
    base = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    print("sitemap 前 5 个仓库：", sitemap_repos(limit=5))
    for r in ("AI-ModelScope/stable-diffusion-v1-5", "AI-ModelScope/lcm-lora-sdxl",
              "Stableyogi/Leather-Onsie"):
        it = to_item(r)
        print(json.dumps(it, ensure_ascii=False, indent=1) if it else f"{r} → 无权重")
