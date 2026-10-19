#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""停止 NeoForgeSD：结束控制台与 SD1.5 动作后端（只杀本项目的进程）。"""
import os
import subprocess
import sys

PATTERNS = ("image_app.py", "webui.py")


def _match(cmdline):
    low = (cmdline or "").lower().replace("\\", "/")
    if "image_app.py" in low and (("ui/image_app.py" in low) or ("neoforgesd" in low)):
        return True
    if "webui.py" in low and (("7860" in low) or ("sd-old-aki" in low)):
        return True
    return False


def via_wmic():
    out = subprocess.run(["wmic", "process", "where", "name='python.exe'",
                          "get", "ProcessId,CommandLine", "/format:csv"],
                         capture_output=True, text=True).stdout
    killed = []
    for line in out.splitlines()[1:]:
        parts = line.rsplit(",", 1)
        if len(parts) != 2:
            continue
        cmd, pid = parts[0], parts[1].strip()
        if pid.isdigit() and _match(cmd):
            try:
                os.kill(int(pid), 9)
                killed.append(pid)
            except Exception:
                pass
    return killed


def via_powershell():
    ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
          "Get-CimInstance Win32_Process | Where-Object {$_.Name -match 'python'} | "
          "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
    out = (r.stdout or b"").decode("utf-8", "replace").strip()
    killed = []
    if not out:
        return killed
    import json
    try:
        data = json.loads(out)
    except Exception:
        return killed
    if isinstance(data, dict):
        data = [data]
    for row in data:
        pid = str(row.get("ProcessId") or "")
        if pid.isdigit() and _match(row.get("CommandLine")):
            try:
                os.kill(int(pid), 9)
                killed.append(pid)
            except Exception:
                pass
    return killed


def main():
    killed = []
    for fn in (via_wmic, via_powershell):
        try:
            killed = fn()
            if killed:
                break
        except Exception as e:
            print(f"[{fn.__name__}] 失败: {e}")
    print("已结束进程: " + (", ".join(killed) if killed else "无（可能已经停了）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
