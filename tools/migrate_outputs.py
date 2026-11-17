# -*- coding: utf-8 -*-
"""一次性迁移：把散在 work 根 / work/images 的老产物搬进 work/outputs/<种类>/<年-月-日>/。

规矩：
  - 只搬历史记录里点得到名的文件（按文件名匹配），别的一概不动；
  - 只移动、不删除；目标已存在同名文件就跳过；
  - 历史记录里的 files 同步改成相对 work 的路径，旧链接不断。
用法：
    python tools/migrate_outputs.py --base E:/NeoForge           # 只看，不动
    python tools/migrate_outputs.py --base E:/NeoForge --apply   # 真搬
"""
import argparse
import json
import os
import shutil
import sys

KIND = {"image": "txt2img", "edit": "img2img", "upscale": "upscale",
        "txt2img": "txt2img", "img2img": "img2img", "inpaint": "inpaint"}


def find_file(work, name):
    """在 work 根和 work/images 里找同名产物，返回第一个命中的路径。"""
    for cand in (os.path.join(work, name), os.path.join(work, "images", name)):
        if os.path.isfile(cand):
            return cand
    return ""


def day_of(ts, path):
    """优先用历史记录的 ts（YYYY-MM-DD HH:MM:SS），没就退回文件修改时间。"""
    if ts and len(ts) >= 10 and ts[4] == "-" and ts[7] == "-":
        return ts[:10]
    import time
    return time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(path)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="E:/NeoForge", help="安装根（含 work/）")
    ap.add_argument("--apply", action="store_true", help="真的搬；不带就是演练")
    a = ap.parse_args()

    work = os.path.join(a.base, "work")
    hist_path = os.path.join(work, "history_image.json")
    if not os.path.isfile(hist_path):
        print("没有历史记录，什么也不用搬：%s" % hist_path)
        return 0
    with open(hist_path, encoding="utf-8") as f:
        items = json.load(f)
    if not isinstance(items, list):
        print("history_image.json 不是数组，停手")
        return 1

    moved = 0
    touched = 0
    for rec in items:
        files = rec.get("files") or []
        sub = rec.get("sub") or KIND.get(rec.get("kind") or "", "txt2img")
        new_files = []
        for name in files:
            name = str(name).replace("\\", "/")
            if "/" in name:                       # 已经是新格式，原样留下
                new_files.append(name)
                continue
            src = find_file(work, name)
            if not src:
                new_files.append(name)            # 文件真没了：路径不动，别伪造
                continue
            day = day_of(rec.get("ts"), src)
            dst_dir = os.path.join(work, "outputs", sub, day)
            dst = os.path.join(dst_dir, name)
            rel = "outputs/%s/%s/%s" % (sub, day, name)
            if os.path.abspath(src) == os.path.abspath(dst):
                new_files.append(rel)
                continue
            if os.path.exists(dst):
                print("  跳过（目标已有）：%s" % rel)
                new_files.append(rel)
                continue
            print("  %s  ->  %s" % (src, rel))
            if a.apply:
                os.makedirs(dst_dir, exist_ok=True)
                shutil.move(src, dst)
            moved += 1
            new_files.append(rel)
        if new_files != files:
            rec["files"] = new_files
            rec["sub"] = sub
            touched += 1

    print("\n%s：%d 个文件%s，%d 条历史记录%s" % (
        "已搬" if a.apply else "演练（加 --apply 才真搬）",
        moved, "" if a.apply else "待搬", touched, "" if a.apply else "待改"))

    if a.apply and touched:
        bak = hist_path + ".bak-migrate"
        if not os.path.exists(bak):
            shutil.copy2(hist_path, bak)
            print("历史记录备份：%s" % bak)
        tmp = hist_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=1)
        os.replace(tmp, hist_path)
        print("历史记录已更新：%s" % hist_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
