#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""提示词中⇄英转换 + 补全 + 词库（纯标准库，零网络也能跑）

数据源（本机 SD 扩展里现成的，不重复造）：
  · a1111-sd-webui-tagcomplete/tags/danbooru.zh_CN_SFW.csv   英文tag,中文   （约 9.9 万条）
  · a1111-sd-webui-tagcomplete/tags/danbooru.csv             tag,分类,出图量,"别名"
  · sd-webui-prompt-all-in-one/group_tags/zh_CN.yaml          中文分类词组（人物/服装/场景…）

能力：
  · zh2en("长发, 微笑的少女") → "long_hair, smile, 1girl"     ← 本地词典优先
  · en2zh("long_hair, smile")  → "长发, 微笑"
  · complete("long_h")         → 按出图量排序的前缀补全
  · promptlib()                → 分组词库（中文名 + 英文 tag），给前端点选
  · 词库没覆盖的片段 → 在线翻译兜底（MyMemory，可关），结果落盘缓存

设计取舍：不引机器学习翻译。SD 提示词是 tag 型，中文用户写的「长发/微笑/俯视」
在 danbooru 中文词典里都有确定对应，查表比机翻准且 0 延迟；只有整句描述才走机翻。
"""
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SD_HOME = os.path.abspath(os.environ.get("NEOFORGE_SD_HOME")
                          or os.path.join(_APP_ROOT, os.pardir, "backend"))
TAG_DIR = os.path.join(SD_HOME, "extensions", "a1111-sd-webui-tagcomplete", "tags")
ZH_CSV = os.path.join(TAG_DIR, "danbooru.zh_CN_SFW.csv")
EN_CSV = os.path.join(TAG_DIR, "danbooru.csv")
GROUP_YAML = os.path.join(SD_HOME, "extensions", "sd-webui-prompt-all-in-one",
                          "group_tags", "zh_CN.yaml")

CJK = re.compile(r"[\u3400-\u9fff]")
SPLIT = re.compile(r"[,，、;；。\n\r\t|]+")
# 匹配阈值：词典里混着大量冷门角色/画师名（中文别名同样长），只认有热度的 tag
MIN_HOT = 300          # 多字词条的最低出图量
MIN_HOT_1 = 200000     # 单字词条要求更热门（"光"→light 会抢走"逆光"这种）
MAX_WIN = 12           # 中文词条最长按 12 字切（词典里有 53 字的长别名，明显是角色名）
WEB_MAX_CALLS = 2      # 一次请求最多几次在线兜底（防 3s+ 卡住）

# 词典的死角（实测：forest 的中文名是「树木」、「俯视」只有 from_above 的绕口译名）。
# 这张手写表优先级最高，专门兜住 SD 高频提示词。
COMMON = {
    # 人物
    "少女": "girl", "女孩": "girl", "男孩子": "boy", "男孩": "boy", "女人": "woman",
    "男人": "man", "情侣": "couple", "一个人": "solo", "单人": "solo",
    # 动作
    "站立": "standing", "站着": "standing", "坐着": "sitting", "躺着": "lying",
    "走路": "walking", "跑步": "running", "跳跃": "jumping", "拥抱": "hug",
    "接吻": "kiss", "挥手": "waving", "回头": "looking back", "俯视": "from above",
    "仰视": "from below", "侧脸": "profile", "闭眼": "closed eyes", "睁眼": "open eyes",
    "看向观众": "looking at viewer", "看向镜头": "looking at viewer",
    # 头部
    "长发": "long hair", "短发": "short hair", "双马尾": "twintails",
    "马尾": "ponytail", "卷发": "curly hair", "刘海": "bangs", "呆毛": "ahoge",
    "银发": "silver hair", "黑发": "black hair", "金发": "blonde hair",
    # 表情/身体
    "微笑": "smile", "大笑": "laughing", "哭泣": "crying", "脸红": "blush",
    "蓝眼睛": "blue eyes", "红眼睛": "red eyes", "白皙皮肤": "pale skin",
    # 服装
    "白衬衫": "white shirt", "校服": "school uniform", "连衣裙": "dress",
    "和服": "kimono", "长裙": "long skirt", "短裙": "skirt", "帽子": "hat",
    "眼镜": "glasses", "围巾": "scarf", "卫衣": "hoodie",
    # 场景
    "森林": "forest", "海边": "beach", "海滩": "beach", "城市": "city",
    "街道": "street", "教室": "classroom", "卧室": "bedroom", "天空": "sky",
    "玉米地": "corn field", "草地": "grass", "花园": "garden", "山顶": "mountain",
    # 时间/天气
    "夜晚": "night", "黄昏": "sunset", "日落": "sunset", "日出": "sunrise",
    "雨天": "rain", "下雪": "snow", "雾": "fog", "晴天": "sunny",
    # 光照
    "逆光": "backlighting", "柔光": "soft lighting", "硬光": "hard lighting",
    "侧光": "rim light", "电影感": "cinematic lighting", "体积光": "volumetric lighting",
    "温暖的午后阳光": "warm afternoon sunlight", "午后阳光": "afternoon sunlight",
    # 质量/风格
    "杰作": "masterpiece", "最佳质量": "best quality", "高清": "highres",
    "超精细": "ultra detailed", "动漫风格": "anime style", "写实": "photorealistic",
    "水彩": "watercolor", "素描": "sketch", "像素画": "pixel art",
    "赛博朋克": "cyberpunk", "厚涂": "impasto",
    # 常见物件/动物（词典里常只有冷门别名）
    "猫": "cat", "狗": "dog", "鸟": "bird", "花": "flower", "树": "tree",
    "山": "mountain", "河": "river", "桌子": "table", "椅子": "chair",
    "书": "book", "剑": "sword", "翅膀": "wings", "尾巴": "tail",
    "天空中的云": "cloud", "云": "cloud", "星星": "star", "月亮": "moon",
}

# 机翻偶尔会"加戏"（实测「少女」被机翻成 naked young girl）——自动翻译结果里出现这些就丢弃
NSFW_GUARD = ("naked", "nude", "nsfw", "sex", "penis", "vagina", "hentai", "nipple",
              "porn", "explicit")

_LOCK = threading.Lock()
_D = {"zh2en": None, "en2zh": None, "post": None, "maxlen": 0, "t": 0, "err": ""}
_WEB_CACHE = {}
_CACHE_FILE = None


def _cache_path():
    global _CACHE_FILE
    if _CACHE_FILE is None:
        work = os.environ.get("NEOFORGE_IMAGE_WORK", os.environ.get(
            "NEOFORGE_WORK", r"D:/aicg/_neoforge_console"))
        os.makedirs(work, exist_ok=True)
        _CACHE_FILE = os.path.join(work, "translate_cache.json")
    return _CACHE_FILE


def _clean_zh(cell):
    """中文列可能写成 ':） :} 微笑' —— 取出所有含汉字的片段作为别名。"""
    out = []
    for tok in re.split(r"[\s/]+", cell.strip()):
        if tok and CJK.search(tok):
            out.append(tok)
    return out


def load(force=False):
    """加载三份数据（约 1.5s / 内存约 30MB），只做一次。

    同一中文可能对应多个英文 tag（"森林"既是场景 forest，也是某角色名的别名）——
    按 danbooru 出图量择优，热门优先，这样冷门角色名抢不走常用词。
    """
    with _LOCK:
        if _D["zh2en"] is not None and not force:
            return _D
        # 1) 先读出图量（用作择优与匹配阈值）
        post = {}
        if os.path.isfile(EN_CSV):
            with open(EN_CSV, encoding="utf-8", errors="replace") as f:
                for line in f:
                    p = line.split(",")
                    if len(p) < 3:
                        continue
                    try:
                        post[p[0].strip()] = int(p[2])
                    except ValueError:
                        pass
        # 2) 中文 ↔ 英文（热门优先）
        zh2en, en2zh, hot = {}, {}, {}
        if os.path.isfile(ZH_CSV):
            with open(ZH_CSV, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.rstrip("\r\n")
                    if not line or "," not in line:
                        continue
                    en, zh = line.split(",", 1)
                    en = en.strip().replace(" ", "_")
                    if not en:
                        continue
                    rank = post.get(en, 0)
                    lst = en2zh.setdefault(en, [])
                    for alias in _clean_zh(zh):
                        if alias not in lst:
                            lst.append(alias)
                        if rank > hot.get(alias, -1):
                            zh2en[alias] = en
                            hot[alias] = rank
        else:
            _D["err"] = "中文词典缺失：%s" % ZH_CSV
        maxlen = min(MAX_WIN, max([len(a) for a in zh2en] or [1]))
        for en in post:                      # 补全池：没有中文名的英文 tag 也要能补
            en2zh.setdefault(en, [])
        # 3) 手写常用表压在最上层（热度给满，永远优先于词典里的冷门别名）
        for zh, en in COMMON.items():
            zh2en[zh] = en
            hot[zh] = 10 ** 9
            lst = en2zh.setdefault(en.replace(" ", "_"), [])
            if zh in lst:
                lst.remove(zh)
            lst.insert(0, zh)
        _D.update(zh2en=zh2en, en2zh=en2zh, post=post, hot=hot, maxlen=maxlen,
                  t=time.time())
        return _D


# ---------------------------------------------------------------- 中文 → 英文
def _is_char_tag(en):
    """danbooru 的角色/作品 tag 长得像 `columbina_(genshin_impact)`，
    中文别名常是「少女」「森林」这种常用词 —— 除非整块精确命中，否则不参与切词。"""
    return "(" in en or en.count("_") >= 4


def _pretty(en):
    """词典给的是 danbooru 形式 long_hair；提示词里写 long hair 更顺眼（角色名不动）。"""
    return en if _is_char_tag(en) else en.replace("_", " ")


def _split_zh(chunk, zh2en, hot):
    """把一个中文片段拆成若干英文 tag（最长匹配 + 热度门槛）。

    返回 (tags, rest)：tags 是命中的英文，rest 是没覆盖到的中文残片。
    """
    tags, rest, i, n = [], "", 0, len(chunk)
    while i < n:
        hit = None
        for L in range(min(MAX_WIN, n - i), 1, -1):
            piece = chunk[i:i + L]
            en = zh2en.get(piece)
            if en and hot.get(piece, 0) >= MIN_HOT and not _is_char_tag(en):
                hit = (piece, en)
                break
        if hit is None:
            piece = chunk[i]
            en = zh2en.get(piece)
            floor = MIN_HOT if n == 1 else MIN_HOT_1
            if en and hot.get(piece, 0) >= floor and not _is_char_tag(en):
                hit = (piece, en)
        if hit:
            tags.append(hit[1])
            i += len(hit[0])
        else:
            rest += chunk[i]
            i += 1
    return tags, rest.strip()


_TAG_JUNK = re.compile(r"[\.\?\!\"'“”‘’、，。；;:：]")


def _tagify(s):
    """机翻结果转成提示词形态：小写、去句读、去括号限定、压空格。"""
    s = _TAG_JUNK.sub("", (s or "").strip().lower())
    s = re.sub(r"\([^)]*\)", "", s)
    return re.sub(r"\s+", " ", s).strip(" -_")


def zh2en(text, web=True):
    """中文提示词 → 英文 tag。整句描述里查不到的片段才走在线翻译（web=False 关掉）。"""
    d = load()
    zh2en_ = d["zh2en"] or {}
    hot = d["hot"] or {}
    out, unknown, hits = [], [], []
    for chunk in SPLIT.split(text or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        if not CJK.search(chunk):
            out.append(chunk)                     # 本来就是英文/符号，原样保留
            continue
        if chunk in zh2en_ and hot.get(chunk, 0) >= MIN_HOT:
            en = _pretty(zh2en_[chunk])
            out.append(en)
            hits.append([chunk, en, "dict"])
            continue
        tags, rest = _split_zh(chunk, zh2en_, hot)
        for t in tags:
            t = _pretty(t)
            out.append(t)
            hits.append([chunk, t, "dict"])
        if rest:
            unknown.append(rest)
    misses = []
    if unknown and web:
        frags = [f for f in unknown if len(f) >= 2][:WEB_MAX_CALLS]
        results = {}
        if frags:
            ths = []
            for frag in frags:
                th = threading.Thread(target=lambda f=frag: results.__setitem__(
                    f, _tagify(web_translate(f, "zh", "en"))), daemon=True)
                th.start()
                ths.append(th)
            for th in ths:
                th.join(timeout=9)
        for frag in frags:
            en = results.get(frag)
            if en and len(en) > 2 and not any(w in en for w in NSFW_GUARD):
                out.append(en)
                hits.append([frag, en, "web"])
            else:
                misses.append(frag)
        misses.extend([f for f in unknown if len(f) < 2])
    else:
        misses = list(unknown)
    for frag in misses:
        hits.append([frag, frag, "miss"])
    # 去重但保序
    seen, uniq = set(), []
    for t in out:
        k = t.strip().lower()
        if k and k not in seen:
            seen.add(k)
            uniq.append(t.strip())
    return {"out": ", ".join(uniq), "hits": hits,
            "dict_size": len(zh2en_), "unknown": misses}


# ---------------------------------------------------------------- 英文 → 中文
def en2zh(text):
    d = load()
    e2z = d["en2zh"] or {}
    out, hits = [], []
    for chunk in SPLIT.split(text or ""):
        c = chunk.strip()
        if not c:
            continue
        key = c.replace(" ", "_")
        zh = e2z.get(key) or e2z.get(c)
        if zh:
            out.append(zh[0])
            hits.append([c, zh[0], "dict"])
        else:
            out.append(c)
            hits.append([c, c, "miss"])
    return {"out": "，".join(out), "hits": hits, "dict_size": len(e2z)}


# ---------------------------------------------------------------- 补全
def complete(q, limit=15):
    """英文 tag 前缀 + 中文子串补全，按出图量降序。"""
    d = load()
    q = (q or "").strip().lower()
    if len(q) < 2:
        return {"items": []}
    post = d["post"] or {}
    e2z = d["en2zh"] or {}
    zh_hit = CJK.search(q)
    pool = []
    for en in e2z:
        if zh_hit:
            names = e2z.get(en) or []
            if not any(q in n for n in names):
                continue
            zh = next((n for n in names if q in n), "")
        else:
            if not en.startswith(q.replace(" ", "_")):
                continue
            zh = (e2z.get(en) or [""])[0]
        pool.append((post.get(en, 0), en, zh))
    pool.sort(reverse=True)
    items = [{"tag": en, "zh": zh, "count": c} for c, en, zh in pool[:max(1, min(50, limit))]]
    return {"items": items, "total": len(pool)}


# ---------------------------------------------------------------- 词组库
def promptlib():
    """解析 zh_CN.yaml → [{name, groups:[{name, tags:[{en,zh}]}]}]，失败返回空。"""
    if not os.path.isfile(GROUP_YAML):
        return {"cats": [], "error": "缺 %s" % GROUP_YAML}
    cats, cur_cat, cur_grp = [], None, None
    with open(GROUP_YAML, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\r\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            ind = len(line) - len(line.lstrip())
            s = line.strip()
            if s.startswith("- name:"):
                name = s.split(":", 1)[1].strip()
                if ind <= 2:
                    cur_cat = {"name": name, "groups": []}
                    cats.append(cur_cat)
                    cur_grp = None
                else:
                    cur_grp = {"name": name, "tags": []}
                    if cur_cat is not None:
                        cur_cat["groups"].append(cur_grp)
            elif ":" in s and cur_grp is not None and not s.startswith(("color", "groups", "tags")):
                en, zh = s.split(":", 1)
                en, zh = en.strip(), zh.strip()
                if en and CJK.search(zh or ""):
                    cur_grp["tags"].append({"en": en, "zh": zh})
    return {"cats": cats, "error": ""}


# ---------------------------------------------------------------- 在线兜底
def web_translate(text, frm="zh", to="en", timeout=8):
    """MyMemory 免费接口。只翻词库没覆盖的残片；结果落盘缓存，离线时直接返回 None。"""
    text = (text or "").strip()
    if not text or len(text) > 200:
        return None
    key = f"{frm}>{to}:{text}"
    if key in _WEB_CACHE:
        return _WEB_CACHE[key]
    try:
        with open(_cache_path(), encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    if key in cache:
        _WEB_CACHE[key] = cache[key]
        return cache[key]
    lang = {"zh": "zh-CN", "en": "en"}.get(frm, frm)
    url = ("https://api.mymemory.translated.net/get?q=%s&langpair=%s|%s"
           % (urllib.parse.quote(text), lang, to))
    out = None
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            js = json.loads(r.read().decode("utf-8", "replace"))
        out = ((js.get("responseData") or {}).get("translatedText") or "").strip()
        if not out or out.upper() == text.upper():
            out = None
    except Exception:
        out = None
    _WEB_CACHE[key] = out
    try:
        cache[key] = out
        with open(_cache_path(), "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception:
        pass
    return out


def stats():
    d = load()
    return {"zh_tags": len(d["zh2en"] or {}), "en_tags": len(d["en2zh"] or {}),
            "max_zh_len": d["maxlen"], "load_s": round(time.time() - d["t"], 2) if d["t"] else 0,
            "zh_csv": os.path.isfile(ZH_CSV), "en_csv": os.path.isfile(EN_CSV),
            "group_yaml": os.path.isfile(GROUP_YAML), "error": d["err"]}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print("stats:", json.dumps(stats(), ensure_ascii=False))
    for s in ["长发，微笑的少女，俯视", "白衬衫, 红裙子, 森林, 逆光",
              "masterpiece, best quality, 站立在玉米地里, 温暖的午后阳光"]:
        t0 = time.time()
        r = zh2en(s)
        print("\nZH:", s, "\nEN:", r["out"], "\n   (%.2fs, miss=%s)" % (time.time() - t0, r["unknown"]))
    print("\nEN2ZH:", en2zh("1girl, long_hair, smile, forest")["out"])
    print("\ncomplete:", json.dumps(complete("long_h", 5), ensure_ascii=False))
    print("\ncomplete zh:", json.dumps(complete("长发", 5), ensure_ascii=False))
