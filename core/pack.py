# -*- coding: utf-8 -*-
"""打包：把当前这份壳打成一个可分发的 zip（**不含** backend/ 与 models/）。

    python -m core.pack                    # 打进 <base>/work/pack/
    python -m core.pack --out D:/tmp       # 换落点
    python -m core.pack --no-exe           # 不捎带 dist/NeoForgeSD.exe
    python -m core.pack --verify           # 打完立刻回读校验

包里就三样：

    NeoForgeSD-<版本>/app/…                  源码（含 kernels/、launcher/、core/、ui/、assets/）
    NeoForgeSD-<版本>/NeoForgeSD.exe         有 app/dist/NeoForgeSD.exe 就顺手带上，双击即用
    NeoForgeSD-<版本>/neoforge-package.json  清单：每个文件的 size + sha256

完整包（backend + models，225 GB）不走这里 —— 那条路是 _dist/stage_bundle.py + 7zr 分卷，
壳只负责把自己这件衣服叠好。
"""
import hashlib
import json
import os
import sys
import time
import zipfile

try:                        # 包内导入（启动器/测试用法）
    from . import installer
except ImportError:         # 直接跑脚本
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core import installer

MANIFEST_NAME = installer.MANIFEST_NAME
SCHEMA = installer.SCHEMA

# 打得进去的东西之外，这些都是「跑起来才长出来」或者「不该进包」的
SKIP_DIRS = {
    "__pycache__", ".git", ".github", ".pytest_cache", ".idea", ".vscode",
    "build", "dist", "work", "outputs", "_parked", "node_modules", ".venv", "venv",
}
SKIP_EXT = (".pyc", ".pyo", ".log", ".tmp", ".swp", ".orig", ".rej")
SKIP_SUBSTR = (".bak",)            # launcher.py.bak-singleton 这种备份别进包
SKIP_NAMES = {".DS_Store", "Thumbs.db"}
EXE_CANDIDATES = (
    os.path.join("dist", "NeoForgeSD.exe"),
    "NeoForgeSD.exe",
)

CHUNK = 1 << 20


def app_dir(base_dir):
    """根目录里那份源码在哪：<base>/app 优先，其次 <base> 自己。"""
    for cand in (os.path.join(base_dir, "app"), base_dir):
        if os.path.isfile(os.path.join(cand, "launcher", "neoforge_launcher.py")):
            return cand
    return None


def _walk(src):
    """-> [(绝对路径, 相对路径)]，跳过 SKIP_DIRS / SKIP_EXT / SKIP_NAMES。"""
    out = []
    for root, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for fn in sorted(files):
            if fn in SKIP_NAMES or fn.lower().endswith(SKIP_EXT):
                continue
            if any(s in fn.lower() for s in SKIP_SUBSTR):
                continue
            fp = os.path.join(root, fn)
            rel = os.path.relpath(fp, src).replace(os.sep, "/")   # 包内一律正斜杠
            out.append((fp, rel))
    return out


def find_exe(base_dir):
    """壳的可执行文件（有就带上，没有也能打包 —— 只是装完得用 python 起）。"""
    src = app_dir(base_dir)
    roots = [src, base_dir]
    for r in roots:
        if not r:
            continue
        for rel in EXE_CANDIDATES:
            p = os.path.join(r, rel)
            if os.path.isfile(p):
                return p
    return None


def readme_text(version, root, count, total, has_exe, created):
    """包里的「先看我」—— 抄 PCL2 的思路：整合包要能自己说清怎么起，没启动器的人也能用。"""
    return (
        f"NeoForgeSD {version} · 壳包（不含后端与权重）\n"
        + "=" * 46 + "\n"
        f"打包于 {created} ｜ {count} 个文件 ｜ {total / 1048576:.2f} MiB\n\n"
        "【这里面有什么】\n"
        f"  app\\      前端与业务代码（含 kernels\\ 内核登记册：每个子目录一份 kernel.json）\n"
        + ("  NeoForgeSD.exe  壳（双击即用）\n" if has_exe else "  （没有 exe：用 python 起，见下）\n")
        + "  neoforge-package.json  清单：每个文件的大小与 sha256\n\n"
        "【怎么起】\n"
        + ("  1) 双击 NeoForgeSD.exe\n" if has_exe else "")
        + "  2) 没有 exe 时：python app\\launcher\\neoforge_launcher.py\n\n"
        "【还缺什么】\n"
        "  backend\\   内核一：A1111 运行体（自带 python）—— 把旧的挪到这个目录同级\n"
        "  models\\Stable-diffusion\\   底模（.safetensors / .ckpt）\n"
        "  两样都不在包里：它们几百 GB，走完整包那条路（7z 分卷 + INSTALL.md）\n\n"
        "【怎么再装一份】\n"
        "  把这个 zip 拖进启动器窗口，或拖到 NeoForgeSD.exe 图标上 —— 会自动校验再解开。\n"
        "  装到已装过的目录会就地更新：你改过的文件不动、你删掉的不会自己回来。\n"
    )


def _unique(name, seen):
    """同名条目：.1 .2 … 最多九个，还撞就报错（抄 HMCL Zipper.newEntry 的做法）。"""
    if name in seen:
        base = name
        for i in range(1, 10):
            cand = f"{base}.{i}"
            if cand not in seen:
                seen.add(cand)
                return cand
        raise RuntimeError(f"包里重名条目太多：{name}")
    seen.add(name)
    return name


def _parent_dirs(names):
    out = set()
    for n in names:
        d = os.path.dirname(n)
        while d:
            out.add(d)
            d = os.path.dirname(d)
    return sorted(out)


def build(base_dir, out_path=None, version="0.0.0", include_exe=True, progress=None, log=None):
    """打一个壳包。返回 {path, root, files, bytes, seconds, exe, sha256}。"""
    log = log or (lambda _m: None)

    def emit(frac, text):
        if progress:
            try:
                progress(max(0.0, min(1.0, frac)), text)
            except Exception:
                pass

    src = app_dir(base_dir)
    if not src:
        raise RuntimeError(f"{base_dir} 里找不到 app/launcher/neoforge_launcher.py，没有壳可打")

    created = time.strftime("%Y-%m-%d %H:%M:%S")
    root = f"NeoForgeSD-{version}"
    members = [(p, f"{root}/app/{rel}") for p, rel in _walk(src)]
    exe = find_exe(base_dir) if include_exe else None
    if exe:
        members.append((exe, f"{root}/NeoForgeSD.exe"))

    sizes = []
    for abs_p, _rel in members:
        try:
            sizes.append(os.path.getsize(abs_p))
        except OSError:
            sizes.append(0)
    total = sum(sizes)
    log(f"待打：{len(members)} 个文件，{total / 1048576:.2f} MiB"
        + (f"（含 {os.path.basename(exe)}）" if exe else "（不含 exe）"))

    if not out_path:
        stamp = time.strftime("%Y%m%d-%H%M%S")     # 带秒：同一分钟打两次也不会互相盖掉
        out_path = os.path.join(base_dir, "work", "pack", f"{root}-{stamp}.zip")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    readme_name = f"{root}/README-先看我.txt"
    readme = readme_text(version, root, len(members) + 1, total, bool(exe), created).encode("utf-8")
    all_names = [n for _p, n in members] + [readme_name, f"{root}/{MANIFEST_NAME}"]
    seen = set()

    t0 = time.time()
    done = 0
    entries = []
    tmp_path = out_path + ".part"
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        # 显式目录条目：7-Zip / 资源管理器 / 别的解压器都认得，空目录也不会丢
        for d in _parent_dirs(all_names):
            zf.writestr(_unique(d + "/", seen), b"")

        for idx, ((abs_p, name), size) in enumerate(zip(members, sizes)):
            name = _unique(name, seen)
            h = hashlib.sha256()
            try:
                info = zipfile.ZipInfo.from_file(abs_p, name)     # 时间戳与权限跟着文件走
                with open(abs_p, "rb") as fin, zf.open(info, "w") as fout:
                    while True:
                        buf = fin.read(CHUNK)
                        if not buf:
                            break
                        h.update(buf)
                        fout.write(buf)
                        done += len(buf)
                        emit(done / total if total else 1.0, name)
            except OSError as e:
                log(f"  !! 读不了 {abs_p}：{e}")
                sizes[idx] = 0
                continue
            entries.append({"path": name[len(root) + 1:], "size": size, "sha256": h.hexdigest()})

        zf.writestr(_unique(readme_name, seen), readme)
        entries.append({"path": readme_name[len(root) + 1:], "size": len(readme),
                        "sha256": hashlib.sha256(readme).hexdigest()})

        manifest = {
            "schema": SCHEMA,
            "app": "NeoForgeSD",
            "version": version,
            "kind": "app",
            "created": created,
            "root": root,
            "count": len(entries),
            "bytes": sum(e["size"] for e in entries),
            "has_exe": bool(exe),
            "requires": {
                "backend": "可选：<安装目录>/backend（没有就只是个壳，起不了真内核）",
                "models": "可选：<安装目录>/models（权重不随包走）",
            },
            "files": entries,
        }
        zf.writestr(_unique(f"{root}/{MANIFEST_NAME}", seen),
                    json.dumps(manifest, ensure_ascii=False, indent=1))
    os.replace(tmp_path, out_path)

    took = time.time() - t0
    emit(1.0, "打包完成")
    log(f"✓ 打包完成：{out_path}（{len(entries)} 个文件，"
        f"{manifest['bytes'] / 1048576:.2f} MiB，{took:.1f}s）")
    return {
        "path": out_path,
        "root": root,
        "files": len(entries),
        "bytes": manifest["bytes"],
        "seconds": took,
        "exe": exe,
        "sha256": _file_sha256(out_path),
    }


def _file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            buf = f.read(CHUNK)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def verify(zip_path, log=None):
    """回读校验：清单里每个文件的 size + sha256 跟包里实际内容对得上吗。-> (ok, 明细 dict)"""
    log = log or (lambda _m: None)
    from core import installer  # 局部导入：installer 是包格式的单一事实源
    insp = installer.inspect(zip_path)
    if not insp["ok"]:
        return False, insp
    bad, n = [], 0
    with zipfile.ZipFile(zip_path) as zf:
        for e in insp["manifest"]["files"]:
            member = f"{insp['root']}/{e['path']}" if insp["root"] else e["path"]
            try:
                info = zf.getinfo(member)
            except KeyError:
                bad.append(f"缺文件 {e['path']}")
                continue
            if info.file_size != e["size"]:
                bad.append(f"{e['path']} 大小不符（{info.file_size}≠{e['size']}）")
                continue
            h = hashlib.sha256()
            with zf.open(member) as f:
                while True:
                    buf = f.read(CHUNK)
                    if not buf:
                        break
                    h.update(buf)
            if h.hexdigest() != e["sha256"]:
                bad.append(f"{e['path']} 内容不符（sha256 变了）")
            else:
                n += 1
    if bad:
        for b in bad[:5]:
            log(f"✗ {b}")
        return False, {"ok": False, "why": f"{len(bad)} 个文件对不上", "bad": bad}
    log(f"✓ 校验通过：{n} 个文件 size + sha256 全对")
    return True, {"ok": True, "files": n, "root": insp["root"], "manifest": insp["manifest"]}


def launcher_version(base_dir):
    """从 launcher 源码里读 VERSION（命令行打包时不要求人再敲一遍版本号）。"""
    src = app_dir(base_dir)
    if not src:
        return "0.0.0"
    path = os.path.join(src, "launcher", "neoforge_launcher.py")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("VERSION"):
                    q = line.split("=", 1)[1].strip()
                    return q.strip("'\"")
    except OSError:
        pass
    return "0.0.0"


def main(argv):
    from core.selfcheck import base_of
    base = base_of(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out = None
    if "--out" in argv:
        i = argv.index("--out")
        if i + 1 < len(argv):
            out = argv[i + 1]
    version = launcher_version(base)
    for a in argv:
        if a.startswith("--version="):
            version = a.split("=", 1)[1]
    res = build(base, out_path=out, version=version,
                include_exe="--no-exe" not in argv,
                progress=lambda f, t: print(f"  {f * 100:5.1f}%  {t}", end="\r"),
                log=print)
    print()
    if "--verify" in argv:
        ok, detail = verify(res["path"], log=print)
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.exit(main(sys.argv[1:]))
