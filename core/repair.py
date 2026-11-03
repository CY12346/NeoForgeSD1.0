# -*- coding: utf-8 -*-
"""维修：诊断 → 自动修能修的 → 复检 → 出报告。

设计前提：**启动器可能已经起不来**，所以这个模块不依赖界面、不依赖 core 之外的东西，
可以直接从命令行跑（桌面上的「NeoForgeSD 维修.cmd」就是走这条路）：

    python -m core.repair            # 诊断 + 自动修 + 复检
    python -m core.repair --dry-run  # 只看，不动手

修的都是「确定安全、幂等、可重复跑」的东西：补目录、清孤儿内核进程、清坏掉的 __pycache__、
转日志、从 .bak 恢复坏掉的 kernel.json。**不碰**模型、不动后端配置、不删任何用户数据。
"""
import os
import shutil
import sys
import time

try:
    from . import selfcheck
    from . import detect
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core import selfcheck, detect

try:
    import psutil
except ImportError:
    psutil = None

LOG_MAX_MB = 5          # launcher.log 超过这个就转
LOG_KEEP_MB = 1


def _act(kind, ok, detail):
    return {"kind": kind, "ok": ok, "detail": detail}


# ------------------------------------------------------------------ 单项修复
def fix_dirs(base_dir, manifests, dry=False):
    """补上该有的目录：work/、以及每个内核算出来的出图目录。"""
    made, failed = [], []
    targets = [os.path.join(base_dir, "work")]
    for m in manifests:
        if m.get("outputs"):
            targets.append(m["outputs"])
    for d in targets:
        if os.path.isdir(d):
            continue
        if dry:
            made.append(d)
            continue
        try:
            os.makedirs(d, exist_ok=True)
            made.append(d)
        except OSError as e:
            failed.append(f"{d}（{e}）")
    if failed:
        return _act("补目录", False, "；".join(failed))
    if not made:
        return _act("补目录", True, "目录都在，没动")
    verb = "预演：将建" if dry else "建了"
    return _act("补目录", True, f"{verb} {len(made)} 个：{', '.join(os.path.basename(x) or x for x in made)}")


def _listener_procs(port):
    """谁在 LISTEN 这个端口 -> [(pid, psutil.Process)]"""
    if not psutil:
        return []
    out = []
    try:
        for c in psutil.net_connections(kind="inet"):
            if c.status == "LISTEN" and c.laddr and c.laddr.port == port and c.pid:
                try:
                    out.append((c.pid, psutil.Process(c.pid)))
                except psutil.Error:
                    pass
    except (psutil.Error, OSError):
        pass
    return out


def _is_orphan_kernel(proc):
    """是内核进程、且没有活着的启动器父进程 → 孤儿。"""
    try:
        cmd = " ".join(proc.cmdline()).lower()
    except psutil.Error:
        return False
    if "kernel" not in cmd:
        return False
    try:
        parent = proc.parent()
    except psutil.Error:
        parent = None
    if parent is None:
        return True
    try:
        pcmd = " ".join(parent.cmdline()).lower()
    except psutil.Error:
        return True
    return "neoforge_launcher" not in pcmd


def fix_orphans(manifests, dry=False):
    """端口被孤儿内核进程占着 → 清掉。正经在跑的内核（父进程是启动器）不动。"""
    killed, kept = [], []
    for m in manifests:
        for pid, proc in _listener_procs(m["port"]):
            if not _is_orphan_kernel(proc):
                kept.append(f"{m['id']}:{pid}")
                continue
            if dry:
                killed.append(f"{m['id']}:{pid}(预演)")
                continue
            try:
                for child in proc.children(recursive=True):
                    child.kill()
                proc.kill()
                killed.append(f"{m['id']}:{pid}")
            except psutil.Error as e:
                kept.append(f"{m['id']}:{pid}(杀不掉：{e})")
    if killed:
        return _act("清孤儿", True, f"清掉 {len(killed)} 个：{', '.join(killed)}")
    if kept:
        return _act("清孤儿", True, f"端口上有进程但不是孤儿，没动：{', '.join(kept)}")
    return _act("清孤儿", True, "没有占着内核端口的孤儿进程")


def fix_pycache(base_dir, dry=False):
    """删掉 __pycache__：.pyc 坏掉是「启动器突然起不来」最常见的低级原因，删了会自动重建。"""
    removed = 0
    for sub in ("core", "launcher", "tests"):
        d = os.path.join(base_dir, "app", sub, "__pycache__")
        if not os.path.isdir(d):
            d = os.path.join(base_dir, sub, "__pycache__")
        if not os.path.isdir(d):
            continue
        if dry:
            removed += 1
            continue
        try:
            shutil.rmtree(d)
            removed += 1
        except OSError:
            pass
    if not removed:
        return _act("清缓存", True, "没有 __pycache__ 要清")
    verb = "预演：将清" if dry else "清了"
    return _act("清缓存", True, f"{verb} {removed} 个 __pycache__（下次启动自动重建）")


def fix_log(base_dir, dry=False):
    path = os.path.join(base_dir, "work", "launcher.log")
    if not os.path.isfile(path):
        return _act("转日志", True, "没有 launcher.log")
    size_mb = os.path.getsize(path) / 1048576
    if size_mb <= LOG_MAX_MB:
        return _act("转日志", True, f"launcher.log {size_mb:.1f} MB，不用转")
    if dry:
        return _act("转日志", True, f"launcher.log {size_mb:.1f} MB，预演：留尾部 {LOG_KEEP_MB} MB")
    try:
        with open(path, "rb") as f:
            f.seek(-int(LOG_KEEP_MB * 1048576), os.SEEK_END)
            tail = f.read()
        with open(path, "wb") as f:
            f.write("[... 维修：日志已截断，保留尾部 ...]\n".encode("utf-8") + tail)
        return _act("转日志", True, f"{size_mb:.1f} MB → 保留尾部 {LOG_KEEP_MB} MB")
    except OSError as e:
        return _act("转日志", False, f"截断失败：{e}")


def fix_manifests(base_dir, dry=False):
    """坏掉的 kernel.json：有 .bak 就从 .bak 恢复，没有就报出来。"""
    fixed, broken = [], []
    for name in ("kernels", os.path.join("app", "kernels")):
        kroot = os.path.join(base_dir, name)
        if not os.path.isdir(kroot):
            continue
        for kid in sorted(os.listdir(kroot)):
            kdir = os.path.join(kroot, kid)
            mf = os.path.join(kdir, "kernel.json")
            if not os.path.isfile(mf):
                continue
            try:
                with open(mf, encoding="utf-8") as f:
                    import json
                    json.load(f)
            except (OSError, ValueError) as e:
                bak = mf + ".bak"
                if os.path.isfile(bak) and not dry:
                    try:
                        shutil.copy2(bak, mf)
                        fixed.append(kid)
                        continue
                    except OSError:
                        pass
                broken.append(f"{kid}（{e}）")
    if broken:
        return _act("修内核清单", False, "修不了，需要人看一眼：" + "；".join(broken))
    if fixed:
        return _act("修内核清单", True, f"从 .bak 恢复了 {', '.join(fixed)}")
    return _act("修内核清单", True, "所有 kernel.json 都是合法 JSON")


# ------------------------------------------------------------------ 总流程
def run(base_dir, dry=False, progress=None, own_ports=()):
    """修 + 复检。返回 (actions, items, level, summary)。"""
    def emit(a):
        if progress:
            try:
                progress(a)
            except Exception:
                pass
        return a

    manifests, _bad = selfcheck._discover(base_dir)
    actions = [
        emit(fix_dirs(base_dir, manifests, dry)),
        emit(fix_orphans(manifests, dry)),
        emit(fix_pycache(base_dir, dry)),
        emit(fix_manifests(base_dir, dry)),
        emit(fix_log(base_dir, dry)),
    ]
    if dry:
        items, level, summary = selfcheck.run_all(base_dir, include_probe=False, own_ports=own_ports)
    else:
        items, level, summary = selfcheck.run_all(base_dir, include_probe=True, own_ports=own_ports)
    return actions, items, level, summary


def report_md(actions, items, level, summary, base_dir, dry=False):
    icon = {detect.GREEN: "✅", detect.YELLOW: "⚠️", detect.RED: "❌"}
    lines = [
        "# NeoForgeSD 维修报告" + ("（预演，未动手）" if dry else ""),
        "",
        f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 根目录：`{base_dir}`",
        "",
        "## 一、做了什么",
        "",
        "| 动作 | 结果 | 说明 |",
        "|---|---|---|",
    ]
    for a in actions:
        lines.append(f"| {a['kind']} | {'✅' if a['ok'] else '❌'} | {a['detail']} |")
    lines += ["", "## 二、修完复检", "", f"结论：{icon[level]} **{summary}**", "",
              "| 结果 | 项目 | 说明 | 处置 |", "|---|---|---|---|"]
    for i in items:
        lines.append(f"| {icon[i['level']]} | {i['label']} | {i['detail']} | {i['fix'] or '—'} |")
    lines.append("")
    return "\n".join(lines)


def write_report(actions, items, level, summary, base_dir, dry=False):
    out_dir = os.path.join(base_dir, "work")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, time.strftime("repair-%Y%m%d-%H%M%S.md"))
    with open(path, "w", encoding="utf-8") as f:
        f.write(report_md(actions, items, level, summary, base_dir, dry))
    return path


def main(argv):
    base_dir = selfcheck.base_of(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    dry = "--dry-run" in argv
    print(f"NeoForgeSD 维修{'（预演）' if dry else ''} · 根目录 {base_dir}\n")

    def show(a):
        print(f"{'✅' if a['ok'] else '❌'} {a['kind']:6s} {a['detail']}")

    actions, items, level, summary = run(base_dir, dry=dry, progress=show)
    path = write_report(actions, items, level, summary, base_dir, dry)

    bad = [i for i in items if i["level"] != detect.GREEN]
    if bad:
        print("\n复检仍有需要留意/不通过的项：")
        for i in bad:
            print(f"  {i['label']}：{i['detail']}")
            if i["fix"]:
                print(f"    → {i['fix']}")
    print(f"\n结论：{summary}")
    print(f"报告：{path}")
    return 2 if level == detect.RED else (1 if level == detect.YELLOW else 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
