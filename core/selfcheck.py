# -*- coding: utf-8 -*-
"""自检：把「这台机器现在能不能干活」拆成一条条可验证的项，出 PASS/WARN/FAIL 报告。

壳只调这个模块，界面不内含任何检查逻辑；也能脱离界面单独跑：

    python -m core.selfcheck          # 人话报告（退出码 0=全绿，1=有黄，2=有红）
    python -m core.selfcheck --json   # 机器读

覆盖：内核登记册 → 每个内核的启动命令/工作目录/端口/出图目录 → 假内核真起真停 → 六项环境侦探。
"""
import json
import os
import socket
import sys
import time

try:                            # 作为包导入（launcher 用法）
    from . import detect, kernel
    from .pipe import KernelRunner
except ImportError:             # 直接跑脚本
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core import detect, kernel
    from core.pipe import KernelRunner

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
IMG_LIMIT = 200000      # 目录里图片多于这个数就不数了，只报下限


def _item(key, label, level, detail, fix=""):
    return {"key": key, "label": label, "level": level, "detail": detail, "fix": fix}


# ------------------------------------------------------------------ 单项检查
def _discover(base_dir):
    """-> (可用 manifest 列表, 坏件说明列表)。跟启动器一样查两个根：<base>/kernels 与 <base>/app/kernels。"""
    ms, bad, seen = [], [], set()
    roots = [base_dir, os.path.join(base_dir, "app")]
    for root in roots:
        if not os.path.isdir(os.path.join(root, "kernels")):
            continue
        for m, err in kernel.discover(root, base_dir=base_dir):
            if m:
                if m["id"] not in seen:
                    seen.add(m["id"])
                    ms.append(m)
            elif err:
                bad.append(err)
    return ms, bad


def base_of(start):
    """从 core/ 或 app/ 出发，找真正的根（有 backend/ 或 models/ 的那层）。"""
    d = os.path.abspath(start)
    for _ in range(3):
        if os.path.isdir(os.path.join(d, "backend")) or os.path.isdir(os.path.join(d, "models")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return os.path.dirname(os.path.abspath(start))


def check_registry(base_dir):
    ok, bad = _discover(base_dir)
    if bad:
        return _item("registry", "内核登记册", detect.RED,
                     f"{len(ok)} 个可用，{len(bad)} 个坏件", "；".join(str(b) for b in bad[:2]))
    if not ok:
        return _item("registry", "内核登记册", detect.RED, "kernels/ 下没有可用内核",
                     "放一个 kernels/<id>/kernel.json，参考 SPEC-kernel.md")
    return _item("registry", "内核登记册", detect.GREEN,
                 f"{len(ok)} 个：{', '.join(m['id'] for m in ok)}")


def check_command(m):
    """命令第一个元素是否存在；工作目录是否存在。"""
    cmd0 = m["command"][0]
    looks_path = os.path.isabs(cmd0) or cmd0.lower().endswith((".exe", ".py", ".bat", ".cmd"))
    if looks_path and not os.path.exists(cmd0):
        return _item(f"cmd:{m['id']}", f"{m['id']} · 启动命令", detect.RED,
                     f"找不到 {cmd0}", "检查 kernel.json 的 command，或补上内核自带的解释器")
    if not os.path.isdir(m["cwd"]):
        return _item(f"cwd:{m['id']}", f"{m['id']} · 工作目录", detect.RED,
                     f"不存在 {m['cwd']}", "检查 kernel.json 的 cwd")
    for extra in m["command"][1:]:
        if extra.lower().endswith(".py") and not os.path.isfile(extra):
            return _item(f"cmd:{m['id']}", f"{m['id']} · 启动脚本", detect.RED,
                         f"找不到 {extra}", "检查 kernel.json 的 command")
    return _item(f"cmd:{m['id']}", f"{m['id']} · 启动命令", detect.GREEN,
                 f"{os.path.basename(cmd0)} · cwd {os.path.basename(m['cwd'])}")


def check_port(m, own_ports=()):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        busy = s.connect_ex(("127.0.0.1", m["port"])) == 0
    if busy and m["port"] in own_ports:
        return _item(f"port:{m['id']}", f"{m['id']} · 端口 {m['port']}", detect.GREEN,
                     "由本启动器占用（内核在跑）")
    if busy:
        return _item(f"port:{m['id']}", f"{m['id']} · 端口 {m['port']}", detect.RED,
                     "被别的进程占用", f"关掉占着 {m['port']} 的程序，或改 kernel.json 的 port")
    return _item(f"port:{m['id']}", f"{m['id']} · 端口 {m['port']}", detect.GREEN, "空闲")


def scan_outputs(path):
    """数图片、找最新一张。返回 (张数, 最新相对路径)。"""
    if not os.path.isdir(path):
        return None, None
    n, newest, newest_ts = 0, None, 0.0
    for root, _dirs, files in os.walk(path):
        for f in files:
            if not f.lower().endswith(IMAGE_EXT):
                continue
            n += 1
            fp = os.path.join(root, f)
            try:
                ts = os.path.getmtime(fp)
            except OSError:
                continue
            if ts > newest_ts:
                newest_ts, newest = ts, os.path.relpath(fp, path)
            if n >= IMG_LIMIT:
                return n, newest
    return n, newest


def check_outputs(m):
    path = m.get("outputs")
    if not path:
        if m.get("dialect") == "custom":        # 自测/一次性内核本来就不出图，别刷黄灯
            return _item(f"out:{m['id']}", f"{m['id']} · 出图目录", detect.GREEN,
                         "未声明（custom 内核，不出图）")
        return _item(f"out:{m['id']}", f"{m['id']} · 出图目录", detect.YELLOW,
                     "kernel.json 没声明 outputs，壳不知道图存哪",
                     '在 kernel.json 里加 "outputs": "{base}/…"')
    n, newest = scan_outputs(path)
    if n is None:
        return _item(f"out:{m['id']}", f"{m['id']} · 出图目录", detect.RED,
                     f"目录不存在：{path}", "首次出图后会自动创建；也可以先手动建目录")
    try:                                    # 可写性：建个临时文件立刻删
        probe = os.path.join(path, ".neoforge_write_probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        writable = True
    except OSError:
        writable = False
    stem = f"{n} 张" + (f" · 最近 {newest}" if newest else " · 还没有图")
    if not writable:
        return _item(f"out:{m['id']}", f"{m['id']} · 出图目录", detect.RED,
                     stem + " · 目录只读", "换一个有写权限的目录，或改权限")
    return _item(f"out:{m['id']}", f"{m['id']} · 出图目录", detect.GREEN, stem)


def probe_kernel(m, timeout=30.0):
    """真起真停一次，验证管道：READY 握手 → 收尸 → 端口释放。谁都能当被检对象，但只该用假内核。"""
    runner = KernelRunner(m)
    if not runner.port_free():
        return _item(f"probe:{m['id']}", f"{m['id']} · 管道自检", detect.RED,
                     f"端口 {m['port']} 已被占用，没法真起真停",
                     "先停掉占着这个端口的进程（多半是上次没收干净的内核）")
    t0 = time.time()
    ok = runner.start(wait_ready=timeout)
    took = time.time() - t0
    if not ok:
        return _item(f"probe:{m['id']}", f"{m['id']} · 管道自检", detect.RED,
                     f"起不来（{took:.1f}s）：{runner.last_error or '见日志'}",
                     "先手动跑一遍内核看报什么错")
    clean, alive = runner.stop()
    if not clean:
        why = f"残留进程 {alive}" if alive else f"端口 {m['port']} 仍被占用"
        return _item(f"probe:{m['id']}", f"{m['id']} · 管道自检", detect.RED,
                     f"能起但停不干净：{why}", "检查内核是否派生了不受管子进程")
    return _item(f"probe:{m['id']}", f"{m['id']} · 管道自检", detect.GREEN,
                 f"真起真停通过：就绪 {took:.1f}s，收尸后端口释放、零孤儿")


# ------------------------------------------------------------------ 汇总
def run_all(base_dir, include_probe=True, own_ports=(), progress=None):
    """跑完整自检。progress(item) 会逐条回调，界面边跑边显示。"""
    def emit(it):
        if progress:
            try:
                progress(it)
            except Exception:
                pass
        return it

    items = [emit(check_registry(base_dir))]
    ms, _bad = _discover(base_dir)
    for m in ms:
        items.append(emit(check_command(m)))
        items.append(emit(check_port(m, own_ports)))
        items.append(emit(check_outputs(m)))

    if include_probe:
        probe = next((m for m in ms if m["id"] == "fake"), None)
        if probe:
            items.append(emit(probe_kernel(probe)))
        else:
            items.append(emit(_item("probe", "管道自检", detect.YELLOW,
                                    "没有假内核可试", "放一个 kernels/fake 做管道自测")))

    # 内核登记册这一项上面已经查过了（更细：能分开报好件/坏件），侦探这边就别重复算一次
    env_checks = [c for c in detect.DEFAULT_CHECKS if c.__name__ != "check_kernels"]
    for it in detect.run_checks(base_dir, checks=env_checks):
        items.append(emit(it))

    level, summary = verdict(items)
    return items, level, summary


def verdict(items):
    n_red = sum(1 for i in items if i["level"] == detect.RED)
    n_yellow = sum(1 for i in items if i["level"] == detect.YELLOW)
    if n_red:
        return detect.RED, f"{n_red} 项不通过，先把红灯修掉"
    if n_yellow:
        return detect.YELLOW, f"{len(items)} 项检查，{n_yellow} 项需留意"
    return detect.GREEN, f"{len(items)} 项检查全部通过，可以开工"


def report_md(items, level, summary, base_dir):
    icon = {detect.GREEN: "✅", detect.YELLOW: "⚠️", detect.RED: "❌"}
    lines = [
        "# NeoForgeSD 自检报告",
        "",
        f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 根目录：`{base_dir}`",
        f"- 结论：{icon[level]} **{summary}**",
        "",
        "| 结果 | 项目 | 说明 | 处置 |",
        "|---|---|---|---|",
    ]
    for i in items:
        lines.append(f"| {icon[i['level']]} | {i['label']} | {i['detail']} | {i['fix'] or '—'} |")
    lines.append("")
    return "\n".join(lines)


def write_report(items, level, summary, base_dir, out_dir=None):
    out_dir = out_dir or os.path.join(base_dir, "work")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, time.strftime("selfcheck-%Y%m%d-%H%M%S.md"))
    with open(path, "w", encoding="utf-8") as f:
        f.write(report_md(items, level, summary, base_dir))
    return path


def main(argv):
    base_dir = base_of(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    as_json = "--json" in argv
    no_probe = "--no-probe" in argv

    def show(it):
        if not as_json:
            icon = {detect.GREEN: "●", detect.YELLOW: "◐", detect.RED: "○"}[it["level"]]
            print(f"{icon} {it['label']:22s} {it['detail']}")
            if it["fix"]:
                print(f"  → {it['fix']}")

    items, level, summary = run_all(base_dir, include_probe=not no_probe, progress=show)
    if as_json:
        print(json.dumps({"level": level, "summary": summary, "items": items},
                         ensure_ascii=False, indent=2))
    else:
        path = write_report(items, level, summary, base_dir)
        print(f"\n结论：{summary}")
        print(f"报告：{path}")
    return 2 if level == detect.RED else (1 if level == detect.YELLOW else 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
