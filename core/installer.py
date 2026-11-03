# -*- coding: utf-8 -*-
"""安装：认包 → 校验 → 解包落地。拖拽自动安装和命令行走的是同一条路。

    python -m core.installer 包.zip                      # 装到包旁边同名目录
    python -m core.installer 包.zip --target D:/SD       # 指定落点
    python -m core.installer 包.zip --no-verify          # 跳过 sha256（不推荐）

规矩（都写死在代码里，不靠人记）：
  - 包里必须有 neoforge-package.json，没有就不是本格式的包，直接拒
  - 先全量校验 sha256 **再** 写盘：坏包的半截文件不会落进目标目录
  - zip-slip 一律拒：绝对路径 / 盘符 / `..` / 符号链接
  - 清单之外的文件也拒：包里多出来的东西不许偷偷落盘
  - 目标盘空间不够就别开工（要 1.1 倍余量）
  - 空包（只有清单没有文件）拒装 —— 装了个空气等于没装

抄来的两条（HMCL，GPL-3.0，只学思路没搬代码）：
  1. 文件名编码要猜：老的压缩工具不写 UTF-8 标记，中文名会变成乱码路径。
     HMCL 的 CompressingUtils.findSuitableEncoding 逐个候选编码严格解码，全过才采用；
     这里同样按 UTF-8 → 本机编码 → GB18030 → Big5 → Shift_JIS → … 的顺序试。
  2. 覆盖已有安装走「三态合并」，而不是无脑覆盖或直接拒绝：
     用户改过的文件不覆盖、用户删掉的文件不复活、其余照更新（详见 install 的注释）。
"""
import hashlib
import json
import locale
import os
import shutil
import sys
import time
import zipfile

MANIFEST_NAME = "neoforge-package.json"
INDEX_NAME = "neoforge-installed.json"      # 装在目标目录里，下次更新靠它判断"用户动没动过"
SCHEMA = "neoforge-package/1"
INDEX_SCHEMA = "neoforge-installed/1"
INBOX_DIR = "drop-inbox"
CHUNK = 1 << 20
VOLUME_EXT = (".7z", ".001", ".002", ".rar", ".gz", ".tar", ".xz")

# 猜文件名编码的顺序：UTF-8 优先，然后本机编码，再是常见东亚/西文
FALLBACK_CHARSETS = ("gb18030", "big5", "shift_jis", "euc-kr", "cp1252", "iso-8859-1")


def _no(why):
    return {"ok": False, "why": why}


def _safe_parts(rel):
    """把包内相对路径拆成安全的一段段；越界就返回错误。"""
    p = rel.replace("\\", "/")
    if p.startswith("/") or p.startswith("//"):
        return None, f"包内有绝对路径：{rel}"
    if len(p) > 1 and p[1] == ":":
        return None, f"包内有盘符路径：{rel}"
    parts = [x for x in p.split("/") if x not in ("", ".")]
    if any(x == ".." for x in parts):
        return None, f"包内有越界路径：{rel}"
    if not parts:
        return None, f"包内有空路径：{rel}"
    return parts, ""


def _is_symlink(info):
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def _sha_stream(f):
    h = hashlib.sha256()
    while True:
        buf = f.read(CHUNK)
        if not buf:
            break
        h.update(buf)
    return h.hexdigest()


def _sha_file(path):
    with open(path, "rb") as f:
        return _sha_stream(f)


# ------------------------------------------------------------------ 名字编码
def _raw_name(info):
    """zipfile 把没标 UTF-8 的名字按 cp437 解；反着编回去就是原始字节（cp437 是满的，无损）。"""
    if info.flag_bits & 0x800:
        return None
    try:
        raw = info.filename.encode("cp437")
    except UnicodeEncodeError:
        return None
    return raw if any(b > 127 for b in raw) else None


def _candidates():
    out = ["utf-8"]
    try:
        out.append(locale.getpreferredencoding(False) or "")
    except Exception:
        pass
    out.extend(FALLBACK_CHARSETS)
    seen, res = set(), []
    for c in out:
        low = (c or "").lower().replace("-", "").replace("_", "")
        if c and low not in seen:
            seen.add(low)
            res.append(c)
    return res


def guess_name_encoding(infos):
    """猜包里文件名用的编码：逐个候选严格解码所有未标记的名字，全过才采用。默认 UTF-8。"""
    raws = [r for r in (_raw_name(i) for i in infos) if r]
    if not raws:
        return "utf-8"
    for enc in _candidates():
        try:
            for raw in raws:
                raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        return enc
    return "utf-8"


def decoded_name(info, encoding):
    """成员真实名字：标了 UTF-8 的原样用，否则用猜出来的编码重新解。"""
    raw = _raw_name(info)
    if raw is None:
        return info.filename
    try:
        return raw.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        return info.filename


# ------------------------------------------------------------------ 认包
def inspect(zip_path):
    """认包：是不是本格式的安装包、清单对不对、路径安不安全。 -> {ok, why, manifest, root, members, dirs, bytes}"""
    zip_path = os.path.abspath(zip_path)
    if not os.path.isfile(zip_path):
        return _no(f"文件不存在：{zip_path}")
    if not zipfile.is_zipfile(zip_path):
        if zip_path.lower().endswith(VOLUME_EXT) or ".7z." in zip_path.lower():
            return _no("这是分卷/其它格式的完整包；启动器只装 .zip 壳包 —— "
                       "完整包（backend + models）按 INSTALL.md 用 7z 解开")
        return _no("不是 zip，认不出是安装包")
    try:
        with zipfile.ZipFile(zip_path) as zf:
            bad = zf.testzip()
            if bad:
                return _no(f"zip 已损坏（{bad} 的 CRC 对不上）")
            infos = zf.infolist()
            enc = guess_name_encoding(infos)
            names = {decoded_name(i, enc): i for i in infos}
            files = [n for n in names if not n.endswith("/")]
            cand = [n for n in files
                    if n.replace("\\", "/").split("/")[-1] == MANIFEST_NAME and n.count("/") <= 1]
            if not cand:
                return _no("包里没有 neoforge-package.json —— 不是 NeoForgeSD 安装包")
            mname = cand[0]
            root = mname.rsplit("/", 1)[0] if "/" in mname else ""
            try:
                manifest = json.loads(zf.read(names[mname]).decode("utf-8-sig"))
            except ValueError as e:
                return _no(f"清单不是合法 JSON：{e}")
            if manifest.get("schema") != SCHEMA:
                return _no(f"清单格式不认：{manifest.get('schema')!r}（本机认 {SCHEMA}）")

            prefix = (root + "/") if root else ""
            members, dirs, expect = {}, [], {}
            for e in manifest.get("files", []):
                expect[e["path"].replace("\\", "/")] = e
            for name, info in names.items():
                if name == mname:
                    continue
                if not name.replace("\\", "/").startswith(prefix):
                    return _no(f"包里有清单目录之外的文件：{name}")
                rel = name.replace("\\", "/")[len(prefix):]
                if not rel:
                    continue
                parts, err = _safe_parts(rel)
                if err:
                    return _no(err)
                if name.endswith("/"):
                    dirs.append(rel.rstrip("/"))
                    continue
                if _is_symlink(info):
                    return _no(f"包里带符号链接，不装：{name}")
                members[rel] = info      # 存 ZipInfo：名字猜过编码，读字节只能靠它
            miss = sorted(set(expect) - set(members))
            extra = sorted(set(members) - set(expect))
            if miss or extra:
                bits = []
                if miss:
                    bits.append(f"缺 {len(miss)} 个（如 {miss[0]}）")
                if extra:
                    bits.append(f"多 {len(extra)} 个（如 {extra[0]}）")
                return _no("清单与实际内容不一致：" + "；".join(bits))
            if not members:
                return _no("包里一个文件都没有（只有清单），装它没意义")
            return {
                "ok": True,
                "why": "",
                "zip": zip_path,
                "root": root,
                "manifest": manifest,
                "members": members,
                "dirs": dirs,
                "name_encoding": enc,
                "bytes": sum(int(e.get("size", 0)) for e in expect.values()),
            }
    except (zipfile.BadZipFile, OSError) as e:
        return _no(f"读不了这个包：{e}")


# ------------------------------------------------------------------ 落点
def installed_index(target):
    """目标目录里有没有上次装的账本（没有 = 陌生目录，别按「更新」处理）。"""
    path = os.path.join(target, INDEX_NAME)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if data.get("schema") != INDEX_SCHEMA or not isinstance(data.get("files"), dict):
        return None
    return data


def default_target(zip_path, root=""):
    """默认落点：包旁边同名目录（重名就 -2/-3，绝不动已有的那份）。"""
    base = os.path.dirname(os.path.abspath(zip_path))
    name = (root or "").strip() or os.path.splitext(os.path.basename(zip_path))[0]
    name = name.replace("/", "_").replace("\\", "_")
    cand = os.path.join(base, name)
    i = 2
    while os.path.exists(cand):
        if os.path.isdir(cand) and not os.listdir(cand):
            return cand
        cand = os.path.join(base, f"{name}-{i}")
        i += 1
    return cand


def plan_target(zip_path, root="", prefer_update=True):
    """落点决策：. -> (目标目录, 'update' | 'new')

    包旁边那个同名目录里如果躺着安装账本，说明是本启动器装过的 —— 就地更新（走三态合并）；
    没有账本的目录不碰（可能是人家自己放的），让路到 -2/-3。
    """
    base = os.path.dirname(os.path.abspath(zip_path))
    name = (root or "").strip() or os.path.splitext(os.path.basename(zip_path))[0]
    name = name.replace("/", "_").replace("\\", "_")
    cand = os.path.join(base, name)
    if prefer_update and os.path.isdir(cand) and installed_index(cand):
        return cand, "update"
    return default_target(zip_path, root), "new"


def write_index(target, insp, extra=None):
    """在目标目录写安装账本：下次更新靠它分辨「用户改过的」和「没动过的」。

    extra 是「新包里已经没有、但磁盘上还在且没被动过」的旧记录 —— 留着它，
    下次如果这个文件又回到包里，还能认出「用户没动过」，不会被当成陌生文件。
    """
    files = {rel: {"sha256": e["sha256"], "size": int(e.get("size", 0))}
             for rel, e in ((x["path"].replace("\\", "/"), x)
                            for x in insp["manifest"].get("files", []))
             if rel in insp["members"]}
    for rel, meta in (extra or {}).items():
        files.setdefault(rel, meta)
    data = {
        "schema": INDEX_SCHEMA,
        "app": insp["manifest"].get("app", "NeoForgeSD"),
        "version": insp["manifest"].get("version", "?"),
        "package": os.path.basename(insp["zip"]),
        "installed": time.strftime("%Y-%m-%d %H:%M:%S"),
        "files": files,
    }
    path = os.path.join(target, INDEX_NAME)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return path


# ------------------------------------------------------------------ 装
def install(zip_path, target=None, verify=True, progress=None, log=None,
            report_dir=None, update=True):
    """校验 + 解包。progress(frac, text)；返回 {ok, target, mode, files, bytes, seconds, ...}

    已有安装（目录里有 neoforge-installed.json）时走**三态合并**，抄的是 HMCL
    ModpackInstallTask 的思路，规矩只有三条：
      · 磁盘上的文件和账本里记的 sha256 一样 → 用户没动过 → 更新成新包的版本
      · 不一样（用户改过）或账本里压根没这文件 → 保留用户的，不覆盖
      · 账本里有、磁盘上没了（用户删了）→ 不复活，尊重删除
    账本里有、新包里没有的 → 也不删，只写进报告的「已失效」清单。
    """
    log = log or (lambda _m: None)

    def emit(frac, text):
        if progress:
            try:
                progress(max(0.0, min(1.0, frac)), text)
            except Exception:
                pass

    insp = inspect(zip_path)
    if not insp["ok"]:
        log(f"✗ 拒绝安装：{insp['why']}")
        return {"ok": False, "why": insp["why"]}

    if target:
        target = os.path.abspath(target)
        mode = "update" if (update and installed_index(target)) else "new"
    else:
        target, mode = plan_target(zip_path, insp["root"], prefer_update=update)

    probe = target
    while probe and not os.path.isdir(probe):
        probe = os.path.dirname(probe)
    try:
        free = shutil.disk_usage(probe or os.path.abspath(os.sep)).free
    except OSError:
        free = None
    need = int(insp["bytes"] * 1.1) + 16 * 1048576
    if free is not None and free < need:
        why = f"目标盘只剩 {free / 2**30:.1f} GB，这个要 {need / 2**30:.1f} GB"
        log(f"✗ {why}")
        return {"ok": False, "why": why}

    t0 = time.time()
    old = installed_index(target) if mode == "update" else None
    old_files = (old or {}).get("files") or {}
    if insp["name_encoding"] != "utf-8":
        log(f"包里文件名不是 UTF-8，按 {insp['name_encoding']} 解（不然中文名会变乱码）")
    log(f"安装包 {os.path.basename(insp['zip'])} · {insp['root'] or '(无根目录)'} · "
        f"{insp['manifest'].get('version', '?')} · {insp['bytes'] / 1048576:.2f} MiB"
        f"{' · 就地更新（上次装过，账本在）' if old else ''}")
    log(f"落点 {target}")

    total = insp["bytes"] or 1
    if verify:
        emit(0.0, "校验中…")
        bad, done = [], 0
        expect = {e["path"].replace("\\", "/"): e for e in insp["manifest"].get("files", [])}
        with zipfile.ZipFile(insp["zip"]) as zf:
            for rel, member in insp["members"].items():
                e = expect.get(rel) or {}
                with zf.open(member) as f:
                    h = hashlib.sha256()
                    while True:
                        buf = f.read(CHUNK)
                        if not buf:
                            break
                        h.update(buf)
                        done += len(buf)
                        emit(0.45 * done / total, f"校验 {rel}")
                if h.hexdigest() != e.get("sha256"):
                    bad.append(rel)
        if bad:
            why = f"{len(bad)} 个文件 sha256 对不上：{', '.join(bad[:3])}（包可能被改过或没下全）"
            log(f"✗ {why}")
            return {"ok": False, "why": why, "bad": bad}
        log(f"✓ 校验通过：{len(insp['members'])} 个文件 size/sha256 全对")

    written = updated = kept = skipped = 0
    kept_list, skipped_list = [], []
    bytes_written = 0
    os.makedirs(target, exist_ok=True)
    base_abs = os.path.abspath(target) + os.sep
    expect = {e["path"].replace("\\", "/"): e for e in insp["manifest"].get("files", [])}
    try:
        with zipfile.ZipFile(insp["zip"]) as zf:
            for d in insp["dirs"]:
                parts, err = _safe_parts(d)
                if not err:
                    os.makedirs(os.path.abspath(os.path.join(target, *parts)), exist_ok=True)
            for rel, member in insp["members"].items():
                parts, err = _safe_parts(rel)
                if err:
                    return {"ok": False, "why": err}
                dest = os.path.abspath(os.path.join(target, *parts))
                if not dest.startswith(base_abs):
                    return {"ok": False, "why": f"越界路径：{rel}"}
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                exists = os.path.isfile(dest)
                if exists:
                    if rel in old_files and _sha_file(dest) == old_files[rel].get("sha256"):
                        updated += 1                     # 用户没动过 → 更新
                    else:
                        kept += 1                        # 用户改过 / 陌生文件 → 保留
                        kept_list.append(rel)
                        continue
                elif rel in old_files:
                    skipped += 1                         # 用户删了 → 不复活
                    skipped_list.append(rel)
                    continue
                with zf.open(member) as fin, open(dest, "wb") as fout:
                    while True:
                        buf = fin.read(CHUNK)
                        if not buf:
                            break
                        fout.write(buf)
                        bytes_written += len(buf)
                        emit(0.45 + 0.55 * bytes_written / total, f"解开 {rel}")
                written += 1
                # 时间戳也带过来（HMCL 的 Zipper 就是这么存的），不然装完一堆文件全是"现在"
                dt = member.date_time
                try:
                    ts = time.mktime(tuple(dt) + (0, 0, -1))
                    os.utime(dest, (ts, ts))
                except (OSError, OverflowError, ValueError):
                    pass
        stale = [p for p in old_files if p not in insp["members"]]
        carried = {}
        for rel in stale:
            dest = os.path.abspath(os.path.join(target, *rel.split("/")))
            if not os.path.isfile(dest):
                continue
            try:
                if _sha_file(dest) == old_files[rel].get("sha256"):
                    carried[rel] = old_files[rel]      # 还在、没被动过 → 账本里继续留着
            except OSError:
                pass
        idx = write_index(target, insp, extra=carried)
    except OSError as e:
        why = f"写盘失败：{e}"
        log(f"✗ {why}")
        return {"ok": False, "why": why}
    finally:
        emit(1.0, "安装完成")

    took = time.time() - t0
    new_count = written - updated
    res = {
        "ok": True,
        "target": target,
        "mode": mode,
        "files": written,
        "new": new_count,
        "bytes": bytes_written,
        "seconds": took,
        "verified": bool(verify),
        "updated": updated,
        "kept": kept,
        "kept_list": kept_list,
        "skipped_deleted": skipped,
        "skipped_list": skipped_list,
        "stale": stale,
        "index": idx,
        "manifest": insp["manifest"],
        "zip": insp["zip"],
    }
    bits = [f"{written} 个写入（{new_count} 新、{updated} 更新）"] if updated else [f"{written} 个写入"]
    if kept:
        bits.append(f"{kept} 个保留（你改过/不认识）")
    if skipped:
        bits.append(f"{skipped} 个没复活（你删过）")
    if stale:
        bits.append(f"{len(stale)} 个已失效（没删，清单里记着）")
    log(f"✓ 装好了：{target}（{'，'.join(bits)}，{bytes_written / 1048576:.2f} MiB，{took:.1f}s）")
    if kept_list:
        log(f"  保留不覆盖：{', '.join(kept_list[:3])}{' …' if len(kept_list) > 3 else ''}")
    if stale:
        log(f"  新包里没有了：{', '.join(stale[:3])}{' …' if len(stale) > 3 else ''}")
    exe = os.path.join(target, "NeoForgeSD.exe")
    if os.path.isfile(exe):
        log(f"  双击即用：{exe}")
    if report_dir:
        res["report"] = write_report(res, report_dir)
    return res


# ------------------------------------------------------------------ 报告
def report_md(res, verify_label):
    m = res.get("manifest") or {}
    lines = [
        "# NeoForgeSD 安装报告",
        "",
        f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 包：`{res.get('zip')}`",
        f"- 版本：{m.get('version', '?')} · 种类 {m.get('kind', '?')} · 打包于 {m.get('created', '?')}",
        f"- 落点：`{res['target']}`（{'就地更新' if res.get('mode') == 'update' else '新装'}）",
        f"- 文件：写入 {res['files']}（新 {res.get('new', res['files'])} · 更新 {res.get('updated', 0)}）"
        f" · 保留 {res.get('kept', 0)} · 未复活 {res.get('skipped_deleted', 0)}"
        f" · 共 {res['bytes'] / 1048576:.2f} MiB · 耗时 {res['seconds']:.1f}s",
        f"- 校验：{verify_label}",
        "",
    ]
    if res.get("kept_list"):
        lines += ["## 保留不覆盖（你改过的 / 账本里没有的）", ""]
        lines += [f"- `{p}`" for p in res["kept_list"]]
        lines.append("")
    if res.get("skipped_list"):
        lines += ["## 没复活（你删掉的）", ""]
        lines += [f"- `{p}`" for p in res["skipped_list"]]
        lines.append("")
    if res.get("stale"):
        lines += ["## 新包里已经没有的文件（没删，只是列出来）", ""]
        lines += [f"- `{p}`" for p in res["stale"]]
        lines.append("")
    lines += ["装完这堆之后：backend/ 与 models/ 仍要单独备（包里不含权重）；"
              "有 `NeoForgeSD.exe` 就双击它，没有就用 `python launcher/neoforge_launcher.py` 起。", ""]
    return "\n".join(lines)


def write_report(res, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, time.strftime("install-%Y%m%d-%H%M%S.md"))
    label = "已过（size + sha256 全对）" if res.get("verified") else "跳过（--no-verify）"
    with open(path, "w", encoding="utf-8") as f:
        f.write(report_md(res, label))
    return path


# ------------------------------------------------------------------ 拖到 exe 上的接力
# 启动器只开一份。第二份进程（拖到 exe 图标、或命令行带包）把路径写进 work/drop-inbox/，
# 已经在跑的那份 1.5 秒内取走 —— 单实例规矩不破，包也不会没人管。
def handoff(paths, work_dir):
    inbox = os.path.join(work_dir, INBOX_DIR)
    os.makedirs(inbox, exist_ok=True)
    path = os.path.join(inbox, f"{int(time.time() * 1000)}-{os.getpid()}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"paths": [os.path.abspath(p) for p in paths]}, f, ensure_ascii=False)
    return path


def take_handoffs(work_dir):
    """取走别人递进来的路径（读完就删）。-> [(文件路径, [paths])]"""
    inbox = os.path.join(work_dir, INBOX_DIR)
    if not os.path.isdir(inbox):
        return []
    out = []
    for fn in sorted(os.listdir(inbox)):
        if not fn.endswith(".json"):
            continue
        fp = os.path.join(inbox, fn)
        try:
            with open(fp, encoding="utf-8") as f:
                paths = json.load(f).get("paths") or []
            out.append((fp, [p for p in paths if isinstance(p, str)]))
        except (OSError, ValueError):
            out.append((fp, []))
        finally:
            try:
                os.remove(fp)
            except OSError:
                pass
    return out


def main(argv):
    args = [a for a in argv if not a.startswith("-")]
    if not args:
        print(__doc__)
        return 2
    target = None
    if "--target" in argv:
        i = argv.index("--target")
        if i + 1 < len(argv):
            target = argv[i + 1]
    zip_path = os.path.abspath(args[0])
    res = install(zip_path, target=target, verify="--no-verify" not in argv,
                  progress=lambda f, t: print(f"  {f * 100:5.1f}%  {t}", end="\r"),
                  log=print, report_dir=os.path.dirname(zip_path),
                  update="--no-update" not in argv)
    print()
    if not res.get("ok"):
        print(f"没装成：{res.get('why')}")
        return 1
    print(f"装好了：{res['target']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
