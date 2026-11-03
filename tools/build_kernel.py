# -*- coding: utf-8 -*-
"""把内核里「借来的」文件从仓库 stage 进去，并出分发包。

    python tools/build_kernel.py            # 构建（stage + 写 manifest + 出 zip）
    python tools/build_kernel.py --verify   # 只回读校验（拿 manifest.json 对每个文件的 sha256）

内核自己的代码（neoforge/*.py、kernel_main.py、kernel.json）是**手写源码**，在本目录维护；
只有下面三件是构建时从仓库复制过来的（页面前端、两个零依赖小件），单源仍在仓库里。
"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # app/
KERNEL_DIR = os.path.join(ROOT, "kernels", "neoforge-sd15")
PKG = os.path.join(KERNEL_DIR, "neoforge")
DIST = os.path.join(ROOT, "dist", "kernels")

# (仓库内源文件, 内核内相对路径) —— 只有这三件是复制品
STAGED = [
    ("ui/image_page.html", "neoforge/ui/image_page.html"),
    ("core/winproc.py", "neoforge/_vendor/winproc.py"),
    ("core/i18n_tags.py", "neoforge/_vendor/i18n_tags.py"),
]

# 旧布局（image_app.py 单体 + app/ 副本）的遗留目录，构建时清掉
LEGACY = ["app"]

SKIP_IN_MANIFEST = ("manifest.json", ".buildinfo.json")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest_of(kernel_dir):
    files = []
    for dirpath, dirnames, filenames in os.walk(kernel_dir):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(filenames):
            if name in SKIP_IN_MANIFEST or name.endswith(".pyc"):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, kernel_dir).replace("\\", "/")
            files.append({"path": rel, "size": os.path.getsize(full), "sha256": sha256(full)})
    files.sort(key=lambda x: x["path"])
    return files


def git_commit():
    try:
        out = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=15)
        return out.stdout.strip() if out.returncode == 0 else "(工作区未提交)"
    except Exception:
        return "(无 git)"


def human(n):
    for unit in ("B", "KiB", "MiB"):
        if n < 1024 or unit == "MiB":
            return ("%.2f %s" % (n, unit)) if unit != "B" else ("%d B" % n)
        n /= 1024.0


def build():
    for legacy in LEGACY:
        p = os.path.join(KERNEL_DIR, legacy)
        if os.path.isdir(p):
            shutil.rmtree(p)
            print("  清掉旧布局：%s/" % legacy)

    total = 0
    for src, rel in STAGED:
        s = os.path.join(ROOT, src)
        if not os.path.isfile(s):
            print("✗ 源文件缺失：%s" % s)
            return 2
        d = os.path.join(KERNEL_DIR, rel)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copy2(s, d)
        total += os.path.getsize(d)

    for dirpath, dirnames, _f in os.walk(KERNEL_DIR):
        for dn in list(dirnames):
            if dn == "__pycache__":
                shutil.rmtree(os.path.join(dirpath, dn), ignore_errors=True)
                dirnames.remove(dn)

    with open(os.path.join(KERNEL_DIR, "kernel.json"), encoding="utf-8") as f:
        kver = json.load(f).get("version", "0")

    files = manifest_of(KERNEL_DIR)
    man = {"id": "neoforge-sd15", "version": kver,
           "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "source_commit": git_commit(), "files": files}
    with open(os.path.join(KERNEL_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=2)

    os.makedirs(DIST, exist_ok=True)
    zpath = os.path.join(DIST, "neoforge-sd15-%s.zip" % kver)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for entry in files:
            z.write(os.path.join(KERNEL_DIR, entry["path"]), "neoforge-sd15/" + entry["path"])
    data = buf.getvalue()
    with open(zpath, "wb") as f:
        f.write(data)

    py = [f for f in files if f["path"].endswith(".py")]
    print("内核目录：%s" % KERNEL_DIR)
    print("  stage 的复制品 %d 个 / %s（源在仓库 ui/、core/）" % (len(STAGED), human(total)))
    print("  手写源码：%d 个 .py（neoforge/ 分层 + kernel_main.py）" % len(py))
    print("  包内文件 %d 个 / %s" % (len(files), human(sum(f["size"] for f in files))))
    print("分发包：%s" % zpath)
    print("  %s / sha256 %s" % (human(len(data)), hashlib.sha256(data).hexdigest()))
    print("源码基点：%s" % man["source_commit"])
    return 0


def verify():
    mpath = os.path.join(KERNEL_DIR, "manifest.json")
    if not os.path.isfile(mpath):
        print("✗ 没有 manifest.json，先构建")
        return 2
    with open(mpath, encoding="utf-8") as f:
        man = json.load(f)
    bad = []
    for entry in man["files"]:
        full = os.path.join(KERNEL_DIR, entry["path"])
        if not os.path.isfile(full):
            bad.append("%s 缺文件" % entry["path"])
        elif os.path.getsize(full) != entry["size"]:
            bad.append("%s 大小不符" % entry["path"])
        elif sha256(full) != entry["sha256"]:
            bad.append("%s sha256 不符" % entry["path"])
    on_disk = {f["path"] for f in manifest_of(KERNEL_DIR)}
    for extra in sorted(on_disk - {f["path"] for f in man["files"]}):
        bad.append("%s 不在清单里" % extra)
    if bad:
        print("✗ VERIFY FAIL")
        for b in bad:
            print("   " + b)
        return 1
    print("✓ VERIFY OK · %d 个文件 %s 全对（构建于 %s · %s）"
          % (len(man["files"]), human(sum(f["size"] for f in man["files"])),
             man["built_at"], man["source_commit"]))
    return 0


if __name__ == "__main__":
    sys.exit(verify() if "--verify" in sys.argv else build())
