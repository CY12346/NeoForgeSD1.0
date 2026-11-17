# -*- coding: utf-8 -*-
"""产线卫生（hygiene）：产物落点检查 + 把散在 work 根的东西归回该在的子目录。

为什么需要这个模块：
    2026-11-17 实测，同一台机器上生成的图落在 6 个互不知情的地方：
    `backend/outputs`、`work/` 根（内核前端直接写）、`work/thumbs`、
    Hermes profile 的 `outputs/aigc`、`D:/AIGC/matting`、`D:/AIGC/sticker/*`。
    结果是「图到底存哪」长期说不清。这个模块把「落点」变成一条可检查、可修复的项。

三条原则：
  1. **只移动，不删除。** 本模块永不删文件。历史归档、重复权重清理只**报**不**动**。
  2. 归位只做无歧义的搬动：`work/` 根 → `work/{images,logs,reports}`。
     跨盘归档、模型去重属于「需要人拍板」的事，scan 报出来，fix 不碰。
  3. 被进程占着的文件（启动器正写着的 launcher.log）搬不动就如实报错，不悄悄跳过。

单独跑：
    python -m core.hygiene            # 人话报告（0=干净，1=有可归位项）
    python -m core.hygiene --fix      # 真的归位
    python -m core.hygiene --dups     # 额外扫模型重复（同名同大小 >100MB，慢）
"""
import os
import re
import shutil
import sys

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
VIDEO_EXT = (".mp4", ".webm", ".mov", ".mkv")
REPORT_RE = re.compile(r"^(install|repair|selfcheck|self-check)-\d{8}")
# work 根本来就该有的运行时小文件：不搬
LIVE_FILES = {
    "launcher.log", "launcher.lock", "history_image.json",
    "setup.json", "popularity.json", "catalog-online.json",
}
MOVE_MAX_MB = 200          # 超过这个体积的单文件只报不搬（怕误搬大模型/大视频）
DUP_MIN_MB = 100           # 重复权重只看 >100MB 的


# ------------------------------------------------------------------ 归类
def classify(name, size_mb):
    """work 根里的一个文件该去哪。返回子目录名，或 None 表示不动。"""
    low = name.lower()
    if low in LIVE_FILES:
        return None
    ext = os.path.splitext(low)[1]
    if ext in IMAGE_EXT or ext in VIDEO_EXT:
        return "images" if size_mb <= MOVE_MAX_MB else None
    if ext == ".log":
        return "logs" if size_mb <= MOVE_MAX_MB else None
    if ext == ".md" and REPORT_RE.match(low):
        return "reports"
    return None


# ------------------------------------------------------------------ 扫描
def scan_layout(base_dir):
    """扫 work 根有没有散落产物。返回 (issues, stats)。

    issue = {key,label,level,detail,fix,files} —— 字段跟 selfcheck 的 _item 一致，
    多一个 files 供修复用。
    """
    issues, stats = [], {"images": 0, "logs": 0, "reports": 0, "skipped": []}
    work = os.path.join(base_dir, "work")
    if not os.path.isdir(work):
        issues.append({
            "key": "layout:work", "label": "产物落点 · work 目录",
            "level": "yellow", "detail": f"work 目录不存在：{work}",
            "fix": "首次启动或跑一次维修会自动创建", "files": [],
        })
        return issues, stats

    buckets = {"images": [], "logs": [], "reports": []}
    for name in sorted(os.listdir(work)):
        p = os.path.join(work, name)
        if not os.path.isfile(p):
            continue
        try:
            size_mb = os.path.getsize(p) / 1048576
        except OSError:
            continue
        dest = classify(name, size_mb)
        if dest is None:
            if size_mb > MOVE_MAX_MB:
                stats["skipped"].append(f"{name}（{size_mb:.0f} MB，超过 {MOVE_MAX_MB} MB 不自动搬）")
            continue
        buckets[dest].append(name)

    labels = {"images": "图片", "logs": "日志", "reports": "运行报告"}
    for key, names in buckets.items():
        stats[key] = len(names)
        if not names:
            continue
        sample = "、".join(names[:3]) + (" …" if len(names) > 3 else "")
        issues.append({
            "key": f"layout:{key}",
            "label": f"产物落点 · work 根散着{labels[key]}",
            "level": "yellow",
            "detail": f"{len(names)} 个文件直接躺在 work 根（{sample}）",
            "fix": f"跑一次维修，归位到 work/{key}/",
            "files": names,
        })

    if stats["skipped"]:
        issues.append({
            "key": "layout:big", "label": "产物落点 · work 根的大文件",
            "level": "yellow",
            "detail": "；".join(stats["skipped"][:5]),
            "fix": "大文件不自动搬，确认该往哪放后手工移", "files": [],
        })

    if not issues:
        issues.append({
            "key": "layout:work", "label": "产物落点 · work 根",
            "level": "green", "detail": "干净（散落文件 0 个）",
            "fix": "", "files": [],
        })
    return issues, stats


def scan_outputs_roots(base_dir, manifests=()):
    """列出全部出图落点，好让人一眼看清「图会落到几个地方」。"""
    roots = [("内核前端", os.path.join(base_dir, "work", "images"))]
    for m in manifests:
        if m.get("outputs"):
            roots.append((m.get("id") or "kernel", m["outputs"]))
    exist = [f"{who} → {path}" for who, path in roots if os.path.isdir(path)]
    return {
        "key": "layout:roots", "label": "产物落点 · 一共几处",
        "level": "green" if len(exist) <= 3 else "yellow",
        "detail": (f"{len(exist)} 处：" + "；".join(exist)) if exist else "还没有任何落点目录",
        "fix": "" if len(exist) <= 3 else "落点超过 3 处就该收敛：成片归 D 盘项目目录，别都堆在 work",
        "files": [],
    }


def scan_dup_weights(base_dir, min_mb=DUP_MIN_MB):
    """同名同大小的大权重 —— 只是候选，内容是否相同必须 md5 验。

    只扫 models/ 下，不做 md5（太慢），返回分组供人决定。
    """
    models = os.path.join(base_dir, "models")
    if not os.path.isdir(models):
        return None
    ext_ok = (".safetensors", ".ckpt", ".pt", ".pth", ".bin")
    groups = {}
    floor = min_mb * 1048576
    for dp, _dn, fns in os.walk(models):
        for f in fns:
            if not f.lower().endswith(ext_ok):
                continue
            p = os.path.join(dp, f)
            try:
                s = os.path.getsize(p)
            except OSError:
                continue
            if s < floor:
                continue
            groups.setdefault((f.lower(), s), []).append(os.path.relpath(p, models))
    dups = {k: v for k, v in groups.items() if len(v) > 1}
    if not dups:
        return {"key": "layout:dups", "label": "产物落点 · 重复权重",
                "level": "green", "detail": "没有同名同大小的大权重", "fix": "", "files": []}
    waste = sum(s * (len(v) - 1) for (_, s), v in dups.items()) / 2 ** 30
    lines = [f"{round(s / 2**30, 2)} GB ×{len(v)}  {k[0]}" for (k, v) in list(dups.items())[:8]]
    return {
        "key": "layout:dups", "label": "产物落点 · 重复权重",
        "level": "yellow",
        "detail": f"{len(dups)} 组同名同大小，最多可回收 {waste:.2f} GB（未验 md5）：" + "；".join(lines),
        "fix": "先 md5 验证内容是否真一致，一致才把多余那份移进 _trash-<日期>/，别直接删",
        "files": [p for v in dups.values() for p in v],
    }


# ------------------------------------------------------------------ 修复
def fix_layout(base_dir, dry=False):
    """把 work 根散落的产物归回 work/{images,logs,reports}。只移动，不删除。"""
    act = {"kind": "归位产物", "ok": True, "detail": ""}
    work = os.path.join(base_dir, "work")
    if not os.path.isdir(work):
        act["detail"] = "没有 work 目录，跳过"
        return act

    moved, failed = {}, []
    for name in sorted(os.listdir(work)):
        src = os.path.join(work, name)
        if not os.path.isfile(src):
            continue
        try:
            size_mb = os.path.getsize(src) / 1048576
        except OSError:
            continue
        dest_dir = classify(name, size_mb)
        if dest_dir is None:
            continue
        target_dir = os.path.join(work, dest_dir)
        dst = os.path.join(target_dir, name)
        n = 1
        while os.path.exists(dst):                       # 同名不覆盖，加后缀
            stem, ext = os.path.splitext(name)
            dst = os.path.join(target_dir, f"{stem}-{n}{ext}")
            n += 1
        if dry:
            moved.setdefault(dest_dir, []).append(name)
            continue
        try:
            os.makedirs(target_dir, exist_ok=True)
            shutil.move(src, dst)
            moved.setdefault(dest_dir, []).append(name)
        except OSError as e:
            failed.append(f"{name}（{e.strerror or e}）")

    bits = [f"{k} {len(v)} 个" for k, v in moved.items()]
    if bits:
        act["detail"] = ("预演：会归位 " if dry else "已归位 ") + "，".join(bits)
    else:
        act["detail"] = "work 根没有需要归位的东西"
    if failed:
        act["ok"] = False
        act["detail"] += "；搬不动的：" + "、".join(failed[:5]) + \
                         "（多半被进程占着，关掉启动器再跑一次）"
    return act


# ------------------------------------------------------------------ 报告
def collect(base_dir, manifests=(), with_dups=False):
    """给 selfcheck 用：所有落点相关问题。"""
    issues, _stats = scan_layout(base_dir)
    issues.append(scan_outputs_roots(base_dir, manifests))
    if with_dups:
        d = scan_dup_weights(base_dir)
        if d:
            issues.append(d)
    return issues


LEVEL_MAP = {"green": "🟢", "yellow": "🟡", "red": "🔴"}


def main(argv):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core import selfcheck
    base = selfcheck.base_of(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    do_fix = "--fix" in argv
    with_dups = "--dups" in argv
    print(f"NeoForgeSD 产物落点体检 · 根目录 {base}\n")

    ms, _bad = selfcheck._discover(base)
    for it in collect(base, ms, with_dups=with_dups):
        print(f"{LEVEL_MAP.get(it['level'], it['level'])} {it['label']}")
        print(f"    {it['detail']}")
        if it["fix"]:
            print(f"    → {it['fix']}")
    if do_fix:
        print("\n--- 归位 ---")
        a = fix_layout(base)
        print(f"{'✅' if a['ok'] else '❌'} {a['kind']}：{a['detail']}")
        print("\n--- 归位后复扫 ---")
        issues, _ = scan_layout(base)
        for it in issues:
            print(f"{LEVEL_MAP.get(it['level'], it['level'])} {it['label']}: {it['detail']}")
        return 0 if a["ok"] else 1
    issues, stats = scan_layout(base)
    return 1 if any(i["level"] == "yellow" for i in issues) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
