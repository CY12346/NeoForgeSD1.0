# -*- coding: utf-8 -*-
"""子进程静音：启动器是 pythonw（没有控制台），任何 subprocess 调用默认都会在
屏幕上闪一个黑框 —— taskkill / netstat / tasklist / nvidia-smi / python -c 全都会。

规矩：本项目里所有 fork 出命令的地方都走这里，前端就永远看不到 cmd。

    from core import winproc
    winproc.run(["taskkill", "/PID", "123", "/T", "/F"])      # 静音版 subprocess.run
    winproc.popen([exe, "launch.py"])                        # 静音版 subprocess.Popen

非 Windows 上这两个函数就是原样的 subprocess，不影响别处跑测试。
"""
import subprocess
import sys

IS_WIN = sys.platform == "win32"

# CREATE_NO_WINDOW：控制台程序不分配控制台（连黑框都不建）
_CREATE_NO_WINDOW = 0x08000000


def no_window_kwargs():
    """给 subprocess 用的静音参数：不新建控制台 + STARTUPINFO 里再要求隐藏。"""
    if not IS_WIN:
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return {
        "startupinfo": si,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", _CREATE_NO_WINDOW),
    }


def run(cmd, **kw):
    """subprocess.run 的静音版。默认抓输出（想自己重定向就传 stdout=）。"""
    if "stdout" not in kw and "capture_output" not in kw:
        kw["capture_output"] = True
    kw.update(no_window_kwargs())
    return subprocess.run(cmd, **kw)


def popen(cmd, **kw):
    """subprocess.Popen 的静音版。"""
    kw.update(no_window_kwargs())
    return subprocess.Popen(cmd, **kw)


def console_windows():
    """当前屏幕上可见的控制台窗口句柄列表（自检/测试用：应该恒为空）。"""
    if not IS_WIN:
        return []
    import ctypes

    u32 = ctypes.WinDLL("user32", use_last_error=True)
    u32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    u32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    found = []

    def _cb(hwnd, _lp):
        if not u32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(64)
        u32.GetClassNameW(hwnd, buf, 64)
        if buf.value in ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"):
            found.append(hwnd)
        return True

    try:
        u32.EnumWindows(enum_proc(_cb), None)
    except Exception:
        return []
    return found
