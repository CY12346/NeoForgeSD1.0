# -*- coding: utf-8 -*-
"""下载器硬测：真 socket + 真 HTTP Range —— 断点续传 / 多源回退 / 大小校验 / 取消 / 安全解包。

    python tests/test_downloader.py

不起外网：本地起一个线程 HTTP 服务，行为跟 ModelScope / codeload 的 Range 语义一致。
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import downloader, kernel, winproc  # noqa: E402

MIB = 1048576


class Handler(BaseHTTPRequestHandler):
    """支持 Range 的极简文件服务；/norange 故意不支持；/missing 404。"""
    big = (b"NeoForgeSD-range-test-" * 64) * 6144      # ≈ 8.5 MB（慢速发，好让取消有戏）
    ranges = []

    def log_message(self, *_a):
        pass

    def _serve(self, data, support_range):
        rng = self.headers.get("Range")
        if support_range and rng and rng.startswith("bytes="):
            Handler.ranges.append(rng)
            start = int(rng.split("=")[1].split("-")[0])
            chunk = data[start:]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(data)-1}/{len(data)}")
        else:
            chunk = data
            self.send_response(200)
        self.send_header("Content-Length", str(len(chunk)))
        self.send_header("Accept-Ranges", "bytes" if support_range else "none")
        self.end_headers()
        step = 256 * 1024
        for i in range(0, len(chunk), step):
            self.wfile.write(chunk[i:i + step])
            self.wfile.flush()
            time.sleep(0.03)          # 限速：本地盘太快，取消/续传就没机会发生

    def do_GET(self):
        if self.path.startswith("/file.bin"):
            self._serve(self.big, True)
        elif self.path.startswith("/norange.bin"):
            self._serve(self.big, False)
        elif self.path.startswith("/small.zip"):
            self._serve(ZIP_BYTES, True)
        else:
            self.send_error(404)


def build_zip():
    buf = os.path.join(tempfile.gettempdir(), "_nf_dl_test.zip")
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("ComfyUI-master/main.py", "print('comfy')\n")
        zf.writestr("ComfyUI-master/comfy/__init__.py", "")
        zf.writestr("ComfyUI-master/comfy/x.py", "x = 1\n")
    with open(buf, "rb") as f:
        data = f.read()
    os.remove(buf)
    return data


ZIP_BYTES = build_zip()


def serve():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def check_full(tmp, base):
    item = {"id": "full", "file": "models/dl/full.bin", "size": len(Handler.big) / MIB,
            "urls": [f"{base}/file.bin"]}
    seen = []
    res = downloader.download(item, tmp, progress=lambda f, t: seen.append(f))
    assert res["ok"], res.get("why")
    path = os.path.join(tmp, "models", "dl", "full.bin")
    assert os.path.getsize(path) == len(Handler.big)
    assert hashlib.sha256(open(path, "rb").read()).hexdigest() == \
        hashlib.sha256(Handler.big).hexdigest(), "内容跟源不一致"
    assert seen and max(seen) >= 0.999, "进度没走到 100%"
    print(f"① 整下：{os.path.getsize(path)} B 全对，进度回调 {len(seen)} 次")


def check_resume(tmp, base):
    Handler.ranges.clear()
    item = {"id": "resume", "file": "models/dl/resume.bin", "size": len(Handler.big) / MIB,
            "urls": [f"{base}/file.bin"]}
    cancel = threading.Event()
    threading.Timer(0.45, cancel.set).start()      # 下到一半喊停（不靠进度回调次数）

    try:
        downloader.download(item, tmp, cancel=cancel)
        raise AssertionError("取消没生效")
    except downloader.Cancelled:
        pass
    part = os.path.join(tmp, "models", "dl", "resume.bin.part")
    assert os.path.isfile(part), "取消后没留下 .part"
    half = os.path.getsize(part)
    print(f"② 取消：留下 .part {half} B")

    res = downloader.download(item, tmp)
    assert res["ok"], res.get("why")
    assert res["resumed_from"] == half, f"没从断点接：{res['resumed_from']} vs {half}"
    assert Handler.ranges, "服务端没收到 Range 请求"
    final = os.path.join(tmp, "models", "dl", "resume.bin")
    assert os.path.getsize(final) == len(Handler.big), "续传后大小不对"
    assert open(final, "rb").read() == Handler.big, "续传后内容不对"
    print(f"③ 续传：Range={Handler.ranges[0]} · 接上 {res['resumed_from']} B · 内容全对")


def check_fallback(tmp, base):
    item = {"id": "fb", "file": "models/dl/fb.bin", "size": len(Handler.big) / MIB,
            "urls": [f"{base}/missing", f"{base}/file.bin"]}
    res = downloader.download(item, tmp)
    assert res["ok"], res.get("why")
    assert res["url"].endswith("/file.bin"), "没落到备用源"
    print("④ 多源回退：第一个 404 → 换源成功")


def check_size_guard(tmp, base):
    item = {"id": "bad", "file": "models/dl/bad.bin", "size": len(Handler.big) / MIB + 5,
            "urls": [f"{base}/file.bin"]}
    res = downloader.download(item, tmp)
    assert not res["ok"], "大小对不上居然算成功"
    assert "大小不对" in (res.get("why") or ""), res.get("why")
    assert not os.path.isfile(os.path.join(tmp, "models", "dl", "bad.bin")), "坏文件被当成下好了"
    print(f"⑤ 大小校验：{res['why'][:48]}")


def check_norange(tmp, base):
    item = {"id": "nr", "file": "models/dl/norange.bin", "size": len(Handler.big) / MIB,
            "urls": [f"{base}/norange.bin"]}
    os.makedirs(os.path.join(tmp, "models", "dl"), exist_ok=True)
    with open(os.path.join(tmp, "models", "dl", "norange.bin.part"), "wb") as f:
        f.write(Handler.big[:1000])          # 假装上次断在这儿
    res = downloader.download(item, tmp, log=lambda m: logs.append(m))
    assert res["ok"], res.get("why")
    assert any("不支持断点续传" in m for m in logs), "没提示服务端不支持断点"
    assert open(os.path.join(tmp, "models", "dl", "norange.bin"), "rb").read() == Handler.big
    print("⑥ 服务端不支持 Range：自动从头下，日志说清了")


def check_archive(tmp, base):
    item = {"id": "zip", "file": "work/downloads/zip.zip", "urls": [f"{base}/small.zip"],
            "archive": True, "unzip_to": "kernels/comfyui", "strip_top": True}
    res = downloader.fetch(item, tmp, log=lambda m: print("   ", m))
    assert res["ok"], res.get("why")
    target = os.path.join(tmp, "kernels", "comfyui")
    assert os.path.isfile(os.path.join(target, "main.py")), "解包没去顶层目录"
    assert os.path.isfile(os.path.join(target, "comfy", "x.py"))
    assert not os.path.exists(os.path.join(target, "ComfyUI-master")), "顶层目录没剥掉"
    assert not os.path.isfile(os.path.join(tmp, "work", "downloads", "zip.zip")), "源码 zip 没清掉"
    print(f"⑦ 解包：{res['files']} 个文件落到 {res['target']}（顶层目录已剥掉）")

    # 越界包必须拒
    evil = os.path.join(tmp, "evil.zip")
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("top/a.txt", "a")
        zf.writestr("top/../../escape.txt", "boom")
    out = downloader.install_archive(evil, os.path.join(tmp, "evil-out"))
    assert not out["ok"] and "越界" in out["why"], out
    print(f"⑧ 越界包被拒：{out['why']}")


def check_7z(tmp):
    """官方整合包是 .7z：拿随包的 7zr 真压一次真解一次，再验自动认 python/入口。"""
    exe = downloader._sevenzip()
    if not exe:
        print("⚠ 没有 7zr.exe，跳过 7z 用例")
        return
    src_root = os.path.join(tmp, "forge-src")
    top = os.path.join(src_root, "webui_forge_cu124_torch24")
    os.makedirs(os.path.join(top, "python_embeded"), exist_ok=True)
    with open(os.path.join(top, "launch.py"), "w", encoding="utf-8") as f:
        f.write("print('forge')\n")
    with open(os.path.join(top, "python_embeded", "python.exe"), "wb") as f:
        f.write(b"MZ fake")
    arc = os.path.join(tmp, "ui-forge.7z")
    r = winproc.run([exe, "a", arc, "webui_forge_cu124_torch24"], cwd=src_root, timeout=120)
    assert r.returncode == 0 and os.path.isfile(arc), f"7zr 压缩失败：{r.returncode}"
    print(f"⓪ 7z 造包：{os.path.getsize(arc)} B")

    target = os.path.join(tmp, "kernels", "forge")
    out = downloader.install_archive7z(arc, target, strip_top=True,
                                       log=lambda m: print("   ", m))
    assert out["ok"], out.get("why")
    assert os.path.isfile(os.path.join(target, "launch.py")), "顶层目录没剥掉"
    assert os.path.isfile(os.path.join(target, "python_embeded", "python.exe")), "python 没搬过来"
    assert not os.path.exists(os.path.join(target, "webui_forge_cu124_torch24")), "顶层目录还在"
    print(f"① 7z 解包：{out['files']} 个文件 → {target}（顶层已剥）")

    found = downloader.guess_kernel(target)
    assert found and found["entry"] == "launch.py", f"没认出入口：{found}"
    assert found["python"].endswith(os.path.join("python_embeded", "python.exe")), found["python"]
    print(f"② 自动认环境：python={os.path.relpath(found['python'], target)} 入口={found['entry']}")

def check_adopt(tmp):
    """浏览器下完拖进来这条路：按文件名认出目录项 → 解开 → 登记内核。"""
    exe = downloader._sevenzip()
    if not exe:
        print("⚠ 没有 7zr.exe，跳过 adopt 用例")
        return
    src_root = os.path.join(tmp, "comfy-src")
    top = os.path.join(src_root, "ComfyUI_windows_portable")
    os.makedirs(os.path.join(top, "python_embeded"), exist_ok=True)
    with open(os.path.join(top, "main.py"), "w", encoding="utf-8") as f:
        f.write("print('comfy')\n")
    with open(os.path.join(top, "python_embeded", "python.exe"), "wb") as f:
        f.write(b"MZ fake")
    arc = os.path.join(tmp, "ComfyUI_windows_portable_nvidia.7z")
    r = winproc.run([exe, "a", arc, "ComfyUI_windows_portable"], cwd=src_root, timeout=120)
    assert r.returncode == 0, "7zr 造包失败"

    hit = downloader.match_archive(arc, tmp)
    assert hit and hit["id"] == "ui-comfyui", f"没认出这是 ComfyUI 的包：{hit}"
    print(f"③ 认包：{os.path.basename(arc)} → {hit['name']}")

    out = downloader.adopt_archive(arc, tmp, log=lambda m: print("   ", m))
    assert out and out["ok"], out
    assert os.path.isfile(os.path.join(out["target"], "main.py")), "入口没解开"
    with open(out["kernel"], encoding="utf-8") as f:
        spec = json.load(f)
    assert spec["command"][0].startswith("{base}/kernels/comfyui/"), spec["command"][0]
    assert spec["port"] == 8188
    print(f"④ 拖进来解好并登记内核：{spec['command'][0]} 端口 {spec['port']}")


def check_kernel(tmp):
    spec = {"id": "comfyui", "name": "ComfyUI", "port": 8188,
            "command": ["{python}", "main.py", "--port", "{port}"],
            "health": {"path": "/system_stats", "interval_s": 2.0, "timeout_s": 3.0,
                       "fail_threshold": 3}}
    path = downloader.write_kernel(tmp, spec, log=lambda m: print("   ", m))
    assert os.path.isfile(path)
    found, bad = [], []
    for m, err in kernel.discover(tmp, base_dir=tmp):
        if m:
            found.append(m)
        else:
            bad.append(err)
    assert found and found[0]["id"] == "comfyui", f"登记册读不出来：{bad}"
    assert found[0]["port"] == 8188, f"端口不对：{found[0]['port']}"
    print(f"⑨ 内核登记：{found[0]['id']} 端口 {found[0]['port']} · 健康 {found[0]['health']['path']}")


logs = []


def main():
    tmp = tempfile.mkdtemp(prefix="neoforge-dl-")
    srv, base = serve()
    print(f"本地源 {base} · 临时目录 {tmp}")
    try:
        check_full(tmp, base)
        check_resume(tmp, base)
        check_fallback(tmp, base)
        check_size_guard(tmp, base)
        check_norange(tmp, base)
        check_archive(tmp, base)
        check_7z(tmp)
        check_adopt(tmp)
        check_kernel(tmp)
    finally:
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nDOWNLOADER OK · 整下/续传/多源/大小校验/无Range/解包/内核登记 全过")


if __name__ == "__main__":
    main()
