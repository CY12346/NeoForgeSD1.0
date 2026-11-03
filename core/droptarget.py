# -*- coding: utf-8 -*-
"""Windows 文件拖放：让启动器窗口接住从资源管理器拖进来的包（WM_DROPFILES）。

Tk 自己不吃文件拖放，这里直接跟 user32 / shell32 打交道：

    DragAcceptFiles(hwnd, True)      告诉系统「这个窗口收文件」
    换掉窗口过程（WNDPROC）          只捞 WM_DROPFILES，其余一律原样转给 Tk
    DragQueryFileW 取路径 + DragFinish 释放

回调走 widget.after_idle：拖放消息确实是在 Tk 主线程的消息循环里到的，但让它排到
当前这批事件之后执行，绝不跟 Tk 自己的消息处理抢线程。

    handle = droptarget.enable(root, on_paths)     # on_paths([路径, ...])
    handle.disable()                               # 关窗/测试收尾

非 Windows 上 enable() 直接返回 None，调用方照旧跑。
"""
import ctypes
import sys

WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4

IS_WIN = sys.platform == "win32"

if IS_WIN:
    from ctypes import wintypes

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM)

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                                       wintypes.WPARAM, wintypes.LPARAM]
    user32.CallWindowProcW.restype = LRESULT
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                   wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = LRESULT
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL

    if ctypes.sizeof(ctypes.c_void_p) == 8:
        _SetWindowLong = user32.SetWindowLongPtrW
        _SetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        _SetWindowLong.restype = ctypes.c_void_p
        _GetWindowLong = user32.GetWindowLongPtrW
        _GetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int]
        _GetWindowLong.restype = ctypes.c_void_p
    else:                                   # 32 位：没有 Ptr 版本
        _SetWindowLong = user32.SetWindowLongW
        _SetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        _SetWindowLong.restype = ctypes.c_long
        _GetWindowLong = user32.GetWindowLongW
        _GetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int]
        _GetWindowLong.restype = ctypes.c_long

    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                      wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT

    shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
    shell32.DragAcceptFiles.restype = None
    shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT,
                                       wintypes.LPWSTR, wintypes.UINT]
    shell32.DragQueryFileW.restype = wintypes.UINT
    shell32.DragFinish.argtypes = [wintypes.HANDLE]
    shell32.DragFinish.restype = None


def paths_from_hdrop(hdrop):
    """HDROP -> [路径, ...]（调用方负责 DragFinish）。"""
    if not IS_WIN:
        return []
    n = shell32.DragQueryFileW(wintypes.HANDLE(hdrop), 0xFFFFFFFF, None, 0)
    out = []
    for i in range(n):
        ln = shell32.DragQueryFileW(wintypes.HANDLE(hdrop), i, None, 0)
        buf = ctypes.create_unicode_buffer(ln + 1)
        shell32.DragQueryFileW(wintypes.HANDLE(hdrop), i, buf, ln + 1)
        if buf.value:
            out.append(buf.value)
    return out


class DropHandle:
    """装着窗口过程的对象 —— 必须留着引用，否则 Python 把回调回收掉，窗口一头撞死在野指针上。"""

    def __init__(self, widget, on_paths, hwnd, old_proc):
        self.widget = widget
        self.on_paths = on_paths
        self.hwnd = hwnd
        self._old = old_proc
        self._proc = None
        self.enabled = True
        self.dropped = 0            # 收过几次（日志/测试用）

    def disable(self):
        if not self.enabled or not IS_WIN:
            return
        try:
            shell32.DragAcceptFiles(wintypes.HWND(self.hwnd), False)
            if self._old:
                _SetWindowLong(wintypes.HWND(self.hwnd), GWLP_WNDPROC, self._old)
        except Exception:
            pass
        self.enabled = False


def enable(widget, on_paths):
    """让这个 Tk 窗口收文件拖放。-> DropHandle（失败返回 None，不影响启动器其它功能）。"""
    if not IS_WIN:
        return None
    try:
        widget.update_idletasks()
        inner = widget.winfo_id()
        hwnd = user32.GetParent(wintypes.HWND(inner)) or inner
        # 真正的绘制区是 inner（Tk 的窗口），标题栏那块是 hwnd：
        # 两处都登记「收文件」，消息落在哪一层都能进 Tk 的消息循环
        shell32.DragAcceptFiles(wintypes.HWND(inner), True)
        shell32.DragAcceptFiles(wintypes.HWND(hwnd), True)
        old = _GetWindowLong(wintypes.HWND(inner), GWLP_WNDPROC)   # 只读，不动窗口
        handle = DropHandle(widget, on_paths, inner, old)

        def _proc(h, msg, wparam, lparam):
            if msg == WM_DROPFILES:
                try:
                    paths = paths_from_hdrop(wparam)
                finally:
                    try:
                        shell32.DragFinish(wintypes.HANDLE(wparam))
                    except Exception:
                        pass
                if paths:
                    handle.dropped += 1
                    try:                       # 排到当前事件之后再动界面
                        widget.after_idle(lambda: on_paths(paths))
                    except Exception:
                        pass
                return 0
            if old:
                return user32.CallWindowProcW(old, h, msg, wparam, lparam)
            return user32.DefWindowProcW(h, msg, wparam, lparam)

        handle._proc = WNDPROC(_proc)
        _SetWindowLong(wintypes.HWND(inner), GWLP_WNDPROC,
                       ctypes.cast(handle._proc, ctypes.c_void_p))
        return handle
    except Exception:
        return None


# ------------------------------------------------------------------ 测试用
def make_hdrop(paths):
    """造一个真的 HDROP（GlobalAlloc 出来的），专给 SendMessage(WM_DROPFILES) 用。"""
    if not IS_WIN:
        return None
    import struct
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.restype = wintypes.BOOL

    text = "".join(p + "\0" for p in paths) + "\0"     # 双 \0 收尾
    raw = text.encode("utf-16-le")
    header = struct.pack("<IiiII", 20, 0, 0, 0, 1)     # pFiles / pt / fNC / fWide=1
    data = header + raw
    GMEM_MOVEABLE = 0x0002
    h = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not h:
        raise OSError("GlobalAlloc 失败")
    ptr = k32.GlobalLock(h)
    ctypes.memmove(ptr, data, len(data))
    k32.GlobalUnlock(h)
    return h


def send_drop(hwnd, hdrop):
    """把 HDROP 当 WM_DROPFILES 发给窗口（同步，回调会在返回前跑完）。"""
    if not IS_WIN:
        return 0
    return user32.SendMessageW(wintypes.HWND(hwnd), WM_DROPFILES,
                               wintypes.WPARAM(hdrop), 0)
