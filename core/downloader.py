# -*- coding: utf-8 -*-
"""下载器：断点续传 · 多源回退 · 进度回调 · 可取消 · 大小校验 · 安全解包。

    python -m core.downloader --list                  # 看看目录里有什么
    python -m core.downloader sd15-emaonly            # 下一个
    python -m core.downloader --all-small             # 小模型全下

真下过的源（2026-10-31，见 core/catalog.py 注释）：
  ModelScope 文件接口（302 → CDN，支持 Range）、codeload 源码 zip、github release 附件（跟随跳转）
github.com 直连在本机是死的，所以目录里不放它。

规矩：
  - 先写 `<文件>.part`，下完校验大小才改名 —— 半截文件不会被当成下好了
  - 断了就留着 .part，下次从断点接着下（服务端不支持 Range 就从头来并在日志里说明）
  - 一个源失败换下一个；全失败才报错
  - 解包复用 installer 的 zip-slip / 符号链接检查，不另写一套
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile

try:                            # 包内导入
    from . import catalog as catalog_mod
    from . import installer
except ImportError:             # 直接跑脚本
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core import catalog as catalog_mod
    from core import installer

CHUNK = 1 << 20
UA = "NeoForgeSD-downloader/0.2 (+local launcher)"
TIMEOUT = 30


class Cancelled(Exception):
    pass


def _mib(n):
    return (n or 0) / 1048576.0


def _human(n):
    if n is None:
        return "未知"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _curl_exe():
    """curl 在哪：本机实测 GitHub release 附件只能靠 curl 走通（urllib 的 TLS 握手会被掐）。"""
    exe = shutil.which("curl")
    if exe:
        return exe
    for c in (r"C:\Windows\System32\curl.exe", r"C:\Windows\SysWOW64\curl.exe"):
        if os.path.isfile(c):
            return c
    return None


def probe(url, timeout=15):
    """问一下这个源有多大、支不支持 Range。-> (总字节|None, 支持Range:bool)

    优先用 curl：只取 2 个字节的表头，绝不让服务端把整包推下来。
    urllib 只在没有 curl 的机器上兜底（GitHub release 附件那条路 urllib 走不通）。
    """
    exe = _curl_exe()
    if exe:
        try:
            from . import winproc
            r = winproc.run([exe, "-sL", "--max-time", str(timeout), "-r", "0-1",
                             "-D", "-", "-o", os.devnull, url], timeout=timeout + 10)
            head = (r.stdout or b"").decode("utf-8", "replace").lower()
            size, has_range = None, False
            for line in head.splitlines():
                if line.startswith("content-range:") and "/" in line:
                    try:
                        size = int(line.rsplit("/", 1)[1].strip())
                        has_range = True
                    except ValueError:
                        pass
                elif line.startswith("content-length:") and size is None:
                    try:
                        size = int(line.split(":", 1)[1].strip())
                    except ValueError:
                        pass
            if size:
                return size, has_range
        except Exception:
            pass
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status = getattr(r, "status", 200)
            cr = r.headers.get("Content-Range") or ""
            cl = r.headers.get("Content-Length")
            try:
                r.read(1)                      # 只拿 1 字节，立刻走人
            except Exception:
                pass
            if status == 206 and "/" in cr:
                try:
                    return int(cr.rsplit("/", 1)[1]), True
                except ValueError:
                    return None, True
            return (int(cl) if cl else None), status == 206
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404, 410):
            raise
        return None, False
    except (urllib.error.URLError, OSError):
        raise


def _emit(progress, frac, text):
    if progress:
        try:
            progress(max(0.0, min(1.0, frac)), text)
        except Exception:
            pass


def _download_curl(exe, urls, part, dest, total, progress, log, cancel):
    """curl 后端：-C - 天然断点续传，进度靠盯 .part 大小（不用解析 curl 输出）。

    退出码 33/36 = 服务端不认 Range（或断点位置对不上）→ 清掉 .part 从头再来一次。
    """
    from . import winproc
    last_err = None
    resumed_from = os.path.getsize(part) if os.path.isfile(part) else 0
    for url in urls:
        for attempt in (0, 1):
            have = os.path.getsize(part) if os.path.isfile(part) else 0
            if total and have >= total > 0:
                have = 0
            if attempt == 1:
                try:
                    os.remove(part)
                except OSError:
                    pass
                have = 0
            if have:
                log(f"  接着上次的断点：已有 {_human(have)}")
            args = [exe, "-L", "--fail", "--connect-timeout", "20", "--retry", "2",
                    "--retry-delay", "2"]
            if have:
                args += ["-C", "-"]
            args += ["-o", part, url]
            proc = winproc.popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            t0 = time.time()
            speed_t, speed_b = t0, have
            while proc.poll() is None:
                if cancel is not None and cancel.is_set():
                    proc.kill()
                    raise Cancelled()
                cur = os.path.getsize(part) if os.path.isfile(part) else 0
                now = time.time()
                speed = ""
                if now - speed_t >= 0.5:
                    speed = f" · {_human((cur - speed_b) / (now - speed_t))}/s"
                    speed_t, speed_b = now, cur
                _emit(progress, (cur / total) if total else 0.0,
                      f"{_human(cur)}/{_human(total)}{speed}")
                time.sleep(0.35)
            err = ""
            try:
                if proc.stderr:
                    err = (proc.stderr.read() or b"").decode("utf-8", "replace").strip()
            except Exception:
                pass
            if proc.returncode == 0:
                got = os.path.getsize(part) if os.path.isfile(part) else 0
                if got == 0:
                    last_err = "下到 0 字节"
                elif total and got != total:
                    last_err = f"大小不对：拿到 {got} B，应为 {total} B"
                else:
                    return {"ok": True, "resumed_from": resumed_from or have, "url": url,
                            "seconds": time.time() - t0}
            else:
                last_err = f"curl 退出码 {proc.returncode} {err[:120]}"
                if proc.returncode in (33, 36) and have:
                    log("  这个源不支持断点续传，从头下")
                    continue
            break
        log(f"  ✗ 这个源不行（{last_err}），换下一个")
    return {"ok": False, "why": last_err or "没有可用的下载源"}


def download(item, base_dir, progress=None, log=None, cancel=None, timeout=TIMEOUT):
    """按目录条目下文件。返回 {ok, path, bytes, seconds, resumed_from, url, why}"""
    log = log or (lambda _m: None)

    def emit(frac, text):
        _emit(progress, frac, text)

    dest = os.path.join(base_dir, item["file"].replace("/", os.sep))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"

    total = int((item.get("size") or 0) * 1048576) or None
    urls = item.get("urls") or []
    if not urls:
        return {"ok": False, "why": "目录条目没有下载源", "path": dest}
    t0 = time.time()

    exe = _curl_exe()
    if exe:
        out = _download_curl(exe, urls, part, dest, total, progress, log, cancel)
        if out.get("ok"):
            os.replace(part, dest)
            took = time.time() - t0
            emit(1.0, "下载完成")
            log(f"  ✓ {os.path.basename(dest)} · {_human(os.path.getsize(dest))} · {took:.1f}s")
            return {"ok": True, "path": dest, "bytes": os.path.getsize(dest), "seconds": took,
                    "resumed_from": out.get("resumed_from", 0), "url": out["url"]}
        return {"ok": False, "why": out.get("why"), "path": dest}

    # ---- 没有 curl 才走 urllib（GitHub release 附件那条路可能不通）
    last_err = None
    resumed_from = 0
    for url in urls:
        have = os.path.getsize(part) if os.path.isfile(part) else 0
        if total and have >= total > 0:
            have = 0
            try:
                os.remove(part)
            except OSError:
                pass
        resumed_from = have
        if have:
            log(f"  接着上次的断点：已有 {_human(have)}")
        try:
            headers = {"User-Agent": UA}
            if have:
                headers["Range"] = f"bytes={have}-"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                status = getattr(r, "status", 200)
                if status == 200 and have:
                    log("  这个源不支持断点续传，从头下")
                    have = 0
                    resumed_from = 0
                if total is None:
                    cr = r.headers.get("Content-Range") or ""
                    if "/" in cr:
                        try:
                            total = int(cr.rsplit("/", 1)[1])
                        except ValueError:
                            total = None
                    else:
                        cl = r.headers.get("Content-Length")
                        total = (int(cl) + have) if cl else None
                mode = "ab" if have else "wb"
                done = have
                speed_t = time.time()
                speed_b = done
                with open(part, mode) as f:
                    while True:
                        if cancel is not None and cancel.is_set():
                            raise Cancelled()
                        buf = r.read(CHUNK)
                        if not buf:
                            break
                        f.write(buf)
                        done += len(buf)
                        frac = (done / total) if total else 0.0
                        now = time.time()
                        speed = ""
                        if now - speed_t >= 0.4:          # 速度每 0.4 s 才重算一次
                            speed = f" · {_human((done - speed_b) / (now - speed_t))}/s"
                            speed_t, speed_b = now, done
                        emit(frac, f"{_human(done)}/{_human(total)}{speed}")
                got = os.path.getsize(part) if os.path.isfile(part) else 0
                if got == 0:
                    raise OSError("下到 0 字节")
                if total and got != total:
                    raise OSError(f"大小不对：拿到 {got} B，应为 {total} B")
            os.replace(part, dest)
            took = time.time() - t0
            emit(1.0, "下载完成")
            log(f"  ✓ {os.path.basename(dest)} · {_human(os.path.getsize(dest))} · {took:.1f}s")
            return {"ok": True, "path": dest, "bytes": os.path.getsize(dest),
                    "seconds": took, "resumed_from": resumed_from, "url": url}
        except Cancelled:
            raise
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
            last_err = f"{type(e).__name__}: {e}"
            log(f"  ✗ 这个源不行（{last_err}），换下一个")
            continue

    return {"ok": False, "why": last_err or "没有可用的下载源", "path": dest}


def install_archive(zip_path, target, strip_top=True, log=None, progress=None):
    """安全解包（复用 installer 的越界/符号链接检查）。strip_top：去掉仓库 zip 的顶层目录。"""
    log = log or (lambda _m: None)
    os.makedirs(target, exist_ok=True)
    base_abs = os.path.abspath(target) + os.sep
    wrote = 0
    with zipfile.ZipFile(zip_path) as zf:
        infos = [i for i in zf.infolist() if not i.filename.endswith("/")]
        if not infos:
            return {"ok": False, "why": "压缩包里没有文件"}
        tops = {i.filename.replace("\\", "/").split("/")[0] for i in infos}
        strip = bool(strip_top and len(tops) == 1 and all("/" in i.filename for i in infos))
        total = sum(i.file_size for i in infos) or 1
        done = 0
        for info in infos:
            name = info.filename.replace("\\", "/")
            if strip:
                name = name.split("/", 1)[1]
            parts, err = installer._safe_parts(name)
            if err:
                return {"ok": False, "why": err}
            if installer._is_symlink(info):
                continue
            dest = os.path.abspath(os.path.join(target, *parts))
            if not dest.startswith(base_abs):
                return {"ok": False, "why": f"越界路径：{name}"}
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(info) as fin, open(dest, "wb") as fout:
                shutil.copyfileobj(fin, fout, CHUNK)
            done += info.file_size
            wrote += 1
            if progress:
                try:
                    progress(done / total, f"解开 {name}")
                except Exception:
                    pass
    log(f"  解包到 {target}（{wrote} 个文件{'，已去掉顶层目录' if strip else ''}）")
    return {"ok": True, "files": wrote, "target": target}


def _sevenzip():
    """7zr.exe 在哪：随包带的 tools/7zr.exe 优先，其次 _MEIPASS、_dist。"""
    cands = []
    meipass = getattr(sys, "_MEIPASS", "")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))          # app/
    for root in (here, os.path.dirname(here), meipass, os.path.dirname(here)):
        if root:
            cands += [os.path.join(root, "tools", "7zr.exe"), os.path.join(root, "7zr.exe")]
    cands.append(r"E:/NeoForge/_dist/7zr.exe")
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def install_archive7z(archive, target, strip_top=True, log=None, progress=None):
    """用随包的 7zr 解 .7z（官方整合包都是 7z）。先解到临时目录再搬 —— 搬的是我们自己控制的路径。"""
    from . import winproc
    log = log or (lambda _m: None)
    exe = _sevenzip()
    if not exe:
        return {"ok": False, "why": "找不到 7zr.exe（随包 tools/7zr.exe 或 _dist/7zr.exe）"}
    os.makedirs(target, exist_ok=True)
    tmp = os.path.abspath(target) + ".unpack"
    if os.path.isdir(tmp):
        shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    log(f"  7z 解包中（{os.path.getsize(archive) / 1048576:.0f} MiB，要一会儿）…")
    r = winproc.run([exe, "x", "-y", "-bso0", "-bsp0", f"-o{tmp}", archive], timeout=3600)
    if r.returncode not in (0, 1):        # 1 = 有警告但解出来了
        shutil.rmtree(tmp, ignore_errors=True)
        msg = (r.stderr or b"").decode("utf-8", "replace")[:200]
        return {"ok": False, "why": f"7z 解包失败（退出码 {r.returncode}）：{msg}"}
    base_abs = tmp + os.sep
    entries = []
    for dp, _dn, fn in os.walk(tmp):
        for f in fn:
            p = os.path.join(dp, f)
            if not os.path.abspath(p).startswith(base_abs):
                return {"ok": False, "why": f"解包跑到了临时目录外面：{p}"}
            entries.append(p)
    tops = {os.path.relpath(e, tmp).replace("\\", "/").split("/")[0] for e in entries}
    strip = bool(strip_top and len(tops) == 1)
    moved = 0
    total = len(entries) or 1
    for i, src in enumerate(entries):
        rel = os.path.relpath(src, tmp)
        if strip:
            parts = rel.replace("\\", "/").split("/")[1:]
            if not parts:
                continue
            rel = os.sep.join(parts)
        dest = os.path.abspath(os.path.join(target, rel))
        if not dest.startswith(os.path.abspath(target) + os.sep):
            return {"ok": False, "why": f"越界路径：{rel}"}
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(src, dest)
        moved += 1
        if progress:
            try:
                progress(0.5 + 0.5 * i / total, f"搬 {os.path.basename(dest)}")
            except Exception:
                pass
    shutil.rmtree(tmp, ignore_errors=True)
    log(f"  7z 解包完成：{moved} 个文件 → {target}{'（已去掉顶层目录）' if strip else ''}")
    return {"ok": True, "files": moved, "target": target}


# 官方整合包里 python 和入口的位置：不一定一样，所以靠找不靠猜
PY_CANDIDATES = (
    r"python_embeded\python.exe",           # ComfyUI portable
    r"system\python\python.exe",            # A1111 / Forge 的新布局
    r"system\python\embedded\python.exe",
    r"python\python.exe",                   # 老 A1111
    r"python.exe",
)
ENTRY_CANDIDATES = ("main.py", "launch.py", "webui.py")


def guess_kernel(target):
    """在一个解好的整合包里认出 python 和入口脚本，好登记成内核。认不出就返回 None。"""
    py = None
    for rel in PY_CANDIDATES:
        p = os.path.join(target, rel)
        if os.path.isfile(p):
            py = p
            break
    if py is None:
        for dp, _dn, fn in os.walk(target):
            if "python.exe" in fn:
                py = os.path.join(dp, "python.exe")
                break
    entry = None
    for name in ENTRY_CANDIDATES:
        if os.path.isfile(os.path.join(target, name)):
            entry = name
            break
    if py is None or entry is None:
        return None
    return {"python": py, "entry": entry}


def write_kernel(base_dir, spec, log=None):
    """把下载好的源码/整合包登记成内核（kernels/<id>/kernel.json）。"""
    log = log or (lambda _m: None)
    kdir = os.path.join(base_dir, "kernels", spec["id"])
    os.makedirs(kdir, exist_ok=True)
    manifest = {
        "spec": 1,
        "id": spec["id"],
        "name": spec.get("name", spec["id"]),
        "version": spec.get("version", "downloaded"),
        "dialect": spec.get("dialect", "custom"),
        "command": spec.get("command") or ["{python}", "main.py"],
        "cwd": spec.get("cwd", "{kernel}"),
        "args": [],
        "env": spec.get("env", {}),
        "port": spec.get("port", 8188),
        "health": spec.get("health", {"path": "/", "interval_s": 2.0, "timeout_s": 3.0,
                                      "fail_threshold": 3}),
        "ready": spec.get("ready", {"mode": "poll"}),
        "outputs": spec.get("outputs"),
        "needs": spec.get("needs", {}),
        "notes": spec.get("notes", "启动器下载的整合包"),
    }
    path = os.path.join(kdir, "kernel.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    log(f"  已登记内核 {spec['id']}（{path}）")
    return path


def fetch(item, base_dir, progress=None, log=None, cancel=None):
    """下载 + 需要的话解包 + 需要的话登记内核。返回 {ok, path, why, files, target}"""
    log = log or (lambda _m: None)
    log(f"下载：{item.get('name', item['id'])}"
        f"（{_human((item.get('size') or 0) * 1048576) if item.get('size') else '大小未知'}）")
    res = download(item, base_dir, progress=progress, log=log, cancel=cancel)
    if not res.get("ok"):
        return res
    if item.get("archive"):
        target = os.path.join(base_dir, item["unzip_to"].replace("/", os.sep))
        if item.get("sevenzip") or res["path"].lower().endswith((".7z", ".7z.001")):
            out = install_archive7z(res["path"], target, strip_top=item.get("strip_top", True),
                                    log=log, progress=progress)
        else:
            out = install_archive(res["path"], target, strip_top=item.get("strip_top", True),
                                  log=log, progress=progress)
        if not out.get("ok"):
            return out
        res["target"] = target
        res["files"] = out["files"]
        try:
            os.remove(res["path"])           # 解完就把压缩包清掉，别占地方
        except OSError:
            pass
        spec = item.get("kernel")
        if isinstance(spec, dict) and spec.get("auto"):
            found = guess_kernel(target)
            if not found:
                log("  包里没认出 python.exe / 入口脚本，先不登记内核（手动看 kernel.json 写法）")
            else:
                spec = dict(spec)
                spec.pop("auto", None)
                spec.setdefault("cwd", "{kernel}")
                py_rel = os.path.relpath(found["python"], base_dir).replace("\\", "/")
                runner = "{base}/" + py_rel          # 整合包自带 python：用 {base} 占位符，换机器也不废
                if found["entry"] == "launch.py":
                    spec["command"] = [runner, found["entry"], "--api", "--port", "{port}",
                                       "--skip-install"]
                else:
                    spec["command"] = [runner, found["entry"], "--port", "{port}"]
                spec["needs"] = dict(spec.get("needs") or {}, python_rel=py_rel)
                log(f"  认出来了：python={py_rel} 入口={found['entry']}")
        if isinstance(spec, dict):
            try:
                res["kernel"] = write_kernel(base_dir, spec, log=log)
            except OSError as e:
                log(f"  ⚠ 内核登记没写成功：{e}")
    res["item"] = item["id"]
    return res


def _name_stem(name):
    """'webui_forge_cu124_torch24.7z' -> 'webui_forge'；'ComfyUI_windows_portable_nvidia.7z' -> 'comfyui_windows_portable'。"""
    base = os.path.basename(name or "").lower()
    base = base[:-3] if base.endswith(".7z") else (base[:-4] if base.endswith(".zip") else base)
    return "_".join(base.split("_")[:2])


def match_archive(path, base_dir=None):
    """拖进来的压缩包是不是目录里某一项（按文件名认：精确名或前两段前缀都算）。

    浏览器下完拖进来这条路必须认得出 —— 官方 CDN 在本机整包被掐，这是唯一稳的路。
    """
    name = os.path.basename(path).lower()
    stem = _name_stem(name)
    for it in catalog_mod.load(base_dir):
        if not it.get("archive"):
            continue
        cands = [os.path.basename(it.get("file", ""))]
        cands += [os.path.basename(u.split("?")[0]) for u in (it.get("urls") or [])]
        for c in cands:
            if not c:
                continue
            if name == c.lower() or (stem and stem == _name_stem(c)):
                return it
    return None


def adopt_archive(path, base_dir, log=None, progress=None):
    """把「自己下好的压缩包」按目录项解开 + 登记内核。认不出返回 None。

    为什么要这条：官方 1.8/1.9 GB 整合包在本机实测**只通表头、整包被掐**（curl 0 KiB/s），
    所以「用浏览器下完 → 拖进启动器」必须是能走通的那条路。
    """
    log = log or (lambda _m: None)
    item = match_archive(path, base_dir)
    if not item:
        return None
    target = os.path.join(base_dir, item["unzip_to"].replace("/", os.sep))
    log(f"认出来了：{os.path.basename(path)} → {item['name']}（解到 {target}）")
    if path.lower().endswith((".7z", ".7z.001")) or item.get("sevenzip"):
        out = install_archive7z(path, target, strip_top=item.get("strip_top", True),
                                log=log, progress=progress)
    else:
        out = install_archive(path, target, strip_top=item.get("strip_top", True),
                              log=log, progress=progress)
    if not out.get("ok"):
        return out
    spec = item.get("kernel")
    if isinstance(spec, dict) and spec.get("auto"):
        found = guess_kernel(target)
        if found:
            spec = dict(spec)
            spec.pop("auto", None)
            py_rel = os.path.relpath(found["python"], base_dir).replace("\\", "/")
            runner = "{base}/" + py_rel
            if found["entry"] == "launch.py":
                spec["command"] = [runner, found["entry"], "--api", "--port", "{port}",
                                   "--skip-install"]
            else:
                spec["command"] = [runner, found["entry"], "--port", "{port}"]
            log(f"  认出来了：python={py_rel} 入口={found['entry']}")
    if isinstance(spec, dict):
        try:
            out["kernel"] = write_kernel(base_dir, spec, log=log)
        except OSError as e:
            log(f"  ⚠ 内核登记没写成功：{e}")
    out["target"] = target
    out["item"] = item["id"]
    return out


def installed(item, base_dir):
    """这个条目看起来已经在位了吗（文件在 / 解包目录在）。"""
    if item.get("archive"):
        return os.path.isdir(os.path.join(base_dir, item["unzip_to"].replace("/", os.sep)))
    return os.path.isfile(os.path.join(base_dir, item["file"].replace("/", os.sep)))


def main(argv):
    items = catalog_mod.load()
    if "--list" in argv or not argv:
        for kind in catalog_mod.KIND_ORDER:
            group = catalog_mod.grouped(items).get(kind) or []
            if not group:
                continue
            print(f"\n=== {catalog_mod.KINDS.get(kind, kind)} ===")
            for it in group:
                size = f"{it['size']:.0f} MiB" if it.get("size") else "—"
                keep = " ✓已下" if False else ""
                print(f"  {it['id']:24s} {size:>10s}  {it['name']}")
        print("\n用法：python -m core.downloader <id> [<id>…]   （--base 换根目录）")
        return 0

    base = os.environ.get("NEOFORGE_BASE") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if "--base" in argv:
        i = argv.index("--base")
        if i + 1 < len(argv):
            base = argv[i + 1]
    targets = [a for a in argv if not a.startswith("-")]
    by = catalog_mod.by_id(items)
    rc = 0
    for tid in targets:
        it = by.get(tid)
        if not it:
            print(f"✗ 目录里没有 {tid}")
            rc = 2
            continue
        res = fetch(it, base,
                    progress=lambda f, t: print(f"  {f * 100:5.1f}%  {t}", end="\r"),
                    log=print)
        print()
        if not res.get("ok"):
            print(f"✗ {tid}：{res.get('why')}")
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
