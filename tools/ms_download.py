#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ModelScope 断点续传下载器（本机专用）
用法: python ms_download.py <目标根目录> <清单文件>
清单: 每行 "repo<TAB>仓库内路径<TAB>本地相对路径"
为什么不用 huggingface_hub / modelscope SDK：本机 HF 只有 0.57MB/s，
ModelScope 2.38MB/s，且 API 简单，自己写更可控（可断点续传、可看进度）。
"""
import os
import sys
import time
import urllib.error
import urllib.request

BASE = "https://www.modelscope.cn/api/v1/models/{repo}/repo?Revision=master&FilePath={path}"
UA = {"User-Agent": "curl/8.0"}


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}TB"


def download(repo, path, dest):
    url = BASE.format(repo=repo, path=urllib.request.quote(path, safe=""))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    done = os.path.getsize(dest) if os.path.exists(dest) else 0

    # 先问总大小
    try:
        req = urllib.request.Request(url, headers={**UA, "Range": "bytes=0-0"})
        with urllib.request.urlopen(req, timeout=25) as r:
            cr = r.headers.get("Content-Range", "")
            total = int(cr.split("/")[-1]) if "/" in cr else int(r.headers.get("Content-Length", 0))
    except Exception as e:
        print(f"  ✗ {path} 无法探测大小: {type(e).__name__} {str(e)[:60]}", flush=True)
        return False

    if done == total and total > 0:
        print(f"  ✓ 已完整 {path} ({human(total)})，跳过", flush=True)
        return True
    if done > total:
        print(f"  ! 本地文件比远端大，重下 {path}", flush=True)
        done = 0

    print(f"  ↓ {path}  {human(done)}/{human(total)} 续传中...", flush=True)
    t0, last = time.time(), time.time()
    mode = "ab" if done else "wb"
    try:
        req = urllib.request.Request(url, headers={**UA, "Range": f"bytes={done}-"})
        with urllib.request.urlopen(req, timeout=60) as r, open(dest, mode) as f:
            got = done
            while True:
                chunk = r.read(1024 * 512)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if time.time() - last > 20:
                    spd = (got - done) / (time.time() - t0) / 1e6
                    pct = got / total * 100 if total else 0
                    eta = (total - got) / (spd * 1e6) / 60 if spd > 0 else 0
                    print(f"     {pct:5.1f}%  {human(got)}/{human(total)}  "
                          f"{spd:.2f}MB/s  剩 {eta:.0f} 分", flush=True)
                    last = time.time()
    except Exception as e:
        print(f"  ✗ {path} 中断: {type(e).__name__} {str(e)[:80]}（可重跑续传）", flush=True)
        return False

    ok = os.path.getsize(dest) == total
    print(f"  {'✅' if ok else '⚠'} {path} 完成 {human(os.path.getsize(dest))}", flush=True)
    return ok


def main():
    root, manifest = sys.argv[1], sys.argv[2]
    rows = []
    with open(manifest, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) == 3:
                rows.append(parts)
    print(f"清单 {len(rows)} 个文件；目标根目录 {root}\n", flush=True)
    allok = True
    for repo, path, rel in rows:
        allok &= download(repo, path, os.path.join(root, rel))
    print("\n=== 全部完成 ===" if allok else "\n=== 有文件未完成（重跑本脚本可续传）===", flush=True)
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
