# -*- coding: utf-8 -*-
"""内核登记册：只认 kernel.json，不认内核代码。

设计约束（来自 SPEC-kernel.md）：
  - spec N 与 N-1 兼容，别的版本直接报人话错误
  - 校验失败不兜底、不静默，一次把问题说清
  - 不做 shell 拼接，command 是数组
"""
import json
import os
import re

SPEC = 1
SUPPORTED_SPECS = (0, 1)
REQUIRED = ("spec", "id", "name", "command", "port")

DIALECT_HEALTH = {
    "a1111": "/sdapi/v1/options",
    "comfy": "/system_stats",
    "neoforge": "/api/status",
    "custom": "/healthz",
}


class ManifestError(ValueError):
    """kernel.json 不合规——错误信息要能直接给人看。"""


def _resolve(placeholders, value):
    """把 {base}/{python}/{port}/{kernel} 换掉；不认得的占位符原样留着，让人看见。"""
    if not isinstance(value, str):
        return value
    out = value
    for k, v in placeholders.items():
        out = out.replace("{%s}" % k, str(v))
    return out


def load_manifest(path, base_dir=None):
    """读并校验一份 kernel.json，返回 dict（已展开占位符）。"""
    path = os.path.abspath(path)
    kdir = os.path.dirname(path)
    if base_dir is None:
        base_dir = os.path.dirname(os.path.dirname(kdir))  # kernels/<id>/ -> 根
    try:
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
    except FileNotFoundError:
        raise ManifestError(f"找不到 {path}")
    except json.JSONDecodeError as e:
        raise ManifestError(f"kernel.json 不是合法 JSON：{e}（{path}）")

    for k in REQUIRED:
        if k not in m:
            raise ManifestError(f"缺少必填字段 {k}（{path}）")

    spec = m.get("spec")
    if spec not in SUPPORTED_SPECS:
        raise ManifestError(
            f"spec={spec} 不在支持范围 {SUPPORTED_SPECS}：这份 kernel.json 可能是更新版本写的，请升级启动器")

    if m["id"] != os.path.basename(kdir):
        raise ManifestError(f"id「{m['id']}」必须等于目录名「{os.path.basename(kdir)}」")

    cmd = m.get("command")
    if not isinstance(cmd, list) or not cmd:
        raise ManifestError("command 必须是非空数组，例如 [\"{python}\", \"ui/image_app.py\"]")

    try:
        port = int(m["port"])
    except (TypeError, ValueError):
        raise ManifestError(f"port 必须是整数，现在是 {m.get('port')!r}")
    if not 1 <= port <= 65535:
        raise ManifestError(f"port 越界：{port}")

    m = json.loads(json.dumps(m))  # 深拷贝，别改调用方的对象
    py = os.path.join(base_dir, "backend", "python", "python.exe")
    if not os.path.isfile(py):
        try:
            import sys
            py = sys.executable
        except Exception:
            py = "python"
    ph = {"base": base_dir, "kernel": kdir, "port": port, "python": py}

    m["dir"] = kdir
    m["path"] = path
    m["port"] = port
    m["args"] = [_resolve(ph, a) for a in m.get("args") or []]
    m["env"] = {k: _resolve(ph, v) for k, v in (m.get("env") or {}).items()}
    m["cwd"] = _resolve(ph, m.get("cwd") or "{base}")
    m["command"] = [_resolve(ph, c) for c in cmd]

    # 脚本查找路径：相对路径的 .py 允许在 cwd / {base} / {base}/app / {kernel} 里找，
    # 这样同一份 kernel.json 在「便携包」和「开发仓库」两种布局下都能用。
    search = [m["cwd"], ph["base"], os.path.join(ph["base"], "app"), ph["kernel"]]
    resolved_cmd = []
    for i, c in enumerate(m["command"]):
        if i > 0 and c.lower().endswith(".py") and not os.path.isabs(c):
            for base in search:
                cand = os.path.join(base, c)
                if os.path.isfile(cand):
                    c = cand
                    break
        resolved_cmd.append(c)
    m["command"] = resolved_cmd

    health = m.setdefault("health", {})
    health.setdefault("path", DIALECT_HEALTH.get(m.get("dialect", "custom"), "/healthz"))
    health.setdefault("interval_s", 1.0)
    health.setdefault("timeout_s", 2.0)
    health.setdefault("fail_threshold", 3)
    if not str(health["path"]).startswith("/"):
        raise ManifestError(f"health.path 必须以 / 开头，现在是 {health['path']!r}")

    ready = m.setdefault("ready", {})
    ready.setdefault("mode", "both")
    ready.setdefault("pattern", r"PIPE:READY(?:\s+port=(\d+))?")
    if ready["mode"] not in ("line", "poll", "both"):
        raise ManifestError(f"ready.mode 只能是 line/poll/both，现在是 {ready['mode']!r}")
    try:
        ready["regex"] = re.compile(ready["pattern"])
    except re.error as e:
        raise ManifestError(f"ready.pattern 不是合法正则：{e}")

    prog = m.get("progress") or {}
    prog.setdefault("pattern", r"PIPE:PROGRESS\s+([0-9.]+)\s*(.*)")
    try:
        prog["regex"] = re.compile(prog["pattern"])
    except re.error as e:
        raise ManifestError(f"progress.pattern 不是合法正则：{e}")
    m["progress"] = prog

    stop = m.setdefault("stop", {})
    stop.setdefault("grace_s", 5)
    m.setdefault("needs", {})
    return m


def discover(root, base_dir=None):
    """扫 kernels/*/kernel.json，返回 [(manifest|None, 错误信息)]，坏的不影响好的。"""
    out = []
    kroot = os.path.join(root, "kernels") if os.path.isdir(os.path.join(root, "kernels")) else root
    if not os.path.isdir(kroot):
        return out
    for name in sorted(os.listdir(kroot)):
        kdir = os.path.join(kroot, name)
        mf = os.path.join(kdir, "kernel.json")
        if not os.path.isdir(kdir) or not os.path.isfile(mf):
            continue
        try:
            out.append((load_manifest(mf, base_dir=base_dir or os.path.dirname(kroot)), None))
        except ManifestError as e:
            out.append((None, f"{name}: {e}"))
    return out
