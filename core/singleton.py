# -*- coding: utf-8 -*-
"""单实例守卫：一个安装目录只开一个壳；一个端口只跑一个核。

两条硬规则，都是「先拦下来、说清楚是谁占着」，绝不悄悄再起第二份：

  1. 壳：Windows 命名互斥体（按安装目录算）+ work/launcher.lock。
     第二次双击不会多开一个窗口，只会把已有窗口叫到前面。
  2. 核：起核前先探端口。端口上已经有进程在听 = 上一份没关干净，
     要么接管停掉，要么别起 —— 4 GB 卡上两份 SD 必有一份被显存打死。

不猜、不兜底：拿不到就返回 None，把「不知道」原样交回去。
"""
import atexit
import ctypes
import hashlib
import json
import os
import socket
import subprocess
import sys
import time

from . import winproc

try:
    import psutil
except ImportError:                     # 打包后可能没有，退到 netstat / tasklist
    psutil = None

IS_WIN = sys.platform == "win32"
ERROR_ALREADY_EXISTS = 183
SW_RESTORE = 9

if IS_WIN:
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    _k32.CreateMutexW.restype = ctypes.c_void_p
    _k32.CloseHandle.argtypes = [ctypes.c_void_p]
    _k32.CloseHandle.restype = ctypes.c_int
    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _u32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
    _u32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    _u32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    _u32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _u32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    _ENUM_PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)


# ------------------------------------------------------------------ 壳：互斥体
def _mutex_name(key):
    """同一份安装目录 → 同一个互斥体名；不同安装（仓库/便携包）互不干扰。"""
    h = hashlib.sha1(os.path.abspath(key).lower().encode("utf-8")).hexdigest()[:12]
    return "Local\\NeoForgeSD-%s" % h


class LauncherGuard:
    """持有互斥体句柄 = 持有「本安装的启动器只有一个」这个权利。"""

    def __init__(self, handle, name, lock_path):
        self.handle = handle
        self.name = name
        self.lock_path = lock_path
        self._released = False

    # ---- 锁文件只写给人看（诊断用），权威是互斥体
    def write_lock(self, extra=None):
        info = {"pid": os.getpid(), "mutex": self.name,
                "started": time.strftime("%Y-%m-%d %H:%M:%S"),
                "base": os.path.dirname(os.path.dirname(os.path.abspath(self.lock_path)))}
        info.update(extra or {})
        try:
            os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
            with open(self.lock_path, "w", encoding="utf-8") as f:
                json.dump(info, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def release(self):
        if self._released:
            return
        self._released = True
        try:
            if IS_WIN and self.handle:
                _k32.CloseHandle(self.handle)
        except Exception:
            pass
        try:
            info = read_lock(self.lock_path)
            if info and info.get("pid") == os.getpid():
                os.remove(self.lock_path)
        except OSError:
            pass


def acquire(key, lock_path=None):
    """拿到本安装的启动器所有权。返回 LauncherGuard，已被别人拿着则返回 None。"""
    lock_path = lock_path or os.path.join(key, "work", "launcher.lock")
    if not IS_WIN:
        return _acquire_by_file(lock_path)
    name = _mutex_name(key)
    handle = _k32.CreateMutexW(None, False, name)
    err = ctypes.get_last_error()
    if not handle:
        raise OSError("CreateMutexW 失败：%s" % ctypes.WinError(err))
    if err == ERROR_ALREADY_EXISTS:
        _k32.CloseHandle(handle)
        return None
    g = LauncherGuard(handle, name, lock_path)
    atexit.register(g.release)
    g.write_lock()
    return g


def _acquire_by_file(lock_path):
    """非 Windows 兜底：锁文件里 pid 还活着就算被占。"""
    info = read_lock(lock_path)
    if info and info.get("pid") and _pid_alive(info["pid"]):
        return None
    g = LauncherGuard(None, "", lock_path)
    atexit.register(g.release)
    g.write_lock()
    return g


def read_lock(lock_path):
    try:
        with open(lock_path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def focus_window(title_substr):
    """把已有窗口叫到前面（找不到就返回 False，调用方自己说人话）。"""
    hwnd = find_window(title_substr)
    if not hwnd:
        return False
    try:
        _u32.ShowWindow(hwnd, SW_RESTORE)
        _u32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def find_window(title_substr):
    if not IS_WIN:
        return None
    want = (title_substr or "").lower()
    found = []

    def _cb(hwnd, _lparam):
        if not _u32.IsWindowVisible(hwnd):
            return True
        n = _u32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        _u32.GetWindowTextW(hwnd, buf, n + 1)
        if want in buf.value.lower():
            found.append(hwnd)
            return False
        return True

    try:
        _u32.EnumWindows(_ENUM_PROC(_cb), None)
    except Exception:
        return None
    return found[0] if found else None


# ------------------------------------------------------------------ 核：端口
def port_listening(port, host="127.0.0.1", timeout=0.3):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((host, int(port))) == 0
    except OSError:
        return False


def _listening_conns():
    fn = getattr(psutil, "net_connections", None) or getattr(psutil, "connections", None)
    if fn is None:
        return []
    try:
        return [c for c in fn(kind="inet")
                if getattr(c, "status", "") == psutil.CONN_LISTEN and c.laddr]
    except Exception:
        return []


def port_owner(port):
    """谁在监听这个端口：{pid,name,cmd,exe,started}；没人监听返回 None。"""
    port = int(port)
    for c in _listening_conns():
        if getattr(c.laddr, "port", None) == port and c.pid:
            info = describe(c.pid)
            if info:
                return info
    for pid in _netstat_listen_pids(port):
        info = describe(pid)
        if info:
            return info
    return None


def _netstat_listen_pids(port):
    try:
        out = winproc.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                             timeout=10).stdout.decode("gbk", "replace")
    except Exception:
        return []
    pids = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING":
            if parts[1].rsplit(":", 1)[-1] == str(port):
                try:
                    pids.append(int(parts[4]))
                except ValueError:
                    pass
    return pids


def describe(pid):
    """pid → 人话档案。拿不到就返回 None（不编）。"""
    pid = int(pid)
    info = {"pid": pid, "name": "", "cmd": "", "exe": "", "started": ""}
    if psutil is not None:
        try:
            p = psutil.Process(pid)
            info["name"] = p.name()
            try:
                info["exe"] = p.exe()
            except Exception:
                pass
            try:
                info["cmd"] = " ".join(p.cmdline())
            except Exception:
                pass
            try:
                info["started"] = time.strftime("%Y-%m-%d %H:%M:%S",
                                                time.localtime(p.create_time()))
            except Exception:
                pass
            return info
        except Exception:
            pass
    try:
        out = winproc.run(["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"],
                             capture_output=True, timeout=10).stdout.decode("gbk", "replace")
        for line in out.splitlines():
            if str(pid) in line:
                info["name"] = line.split('","')[0].strip('"')
                break
    except Exception:
        return None
    return info if info["name"] else None


def shorten(text, limit=110):
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def owner_line(owner):
    """一行说清是谁占的。"""
    if not owner:
        return "没人占用"
    bits = ["pid %s · %s" % (owner["pid"], owner.get("name") or "?" )]
    if owner.get("started"):
        bits.append("启动于 " + owner["started"])
    line = " · ".join(bits)
    if owner.get("cmd"):
        line += "\n  " + shorten(owner["cmd"])
    return line


def _pid_alive(pid):
    if psutil is not None:
        try:
            p = psutil.Process(int(pid))
            return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
        except Exception:
            return False
    try:
        out = winproc.run(["tasklist", "/FI", "PID eq %s" % pid],
                             capture_output=True, timeout=10).stdout.decode("gbk", "replace")
        return str(pid) in out
    except Exception:
        return False


def kill_tree(pid, grace=5.0):
    """整棵进程树停掉（先温和后强杀）。返回仍然活着的 pid 列表（空 = 干净）。"""
    pid = int(pid)
    pids = [pid]
    if psutil is not None:
        try:
            p = psutil.Process(pid)
            pids = [c.pid for c in p.children(recursive=True)] + [pid]
            procs = [psutil.Process(x) for x in pids if _pid_alive(x)]
            for q in procs:
                try:
                    q.terminate()
                except Exception:
                    pass
            _, alive = psutil.wait_procs(procs, timeout=grace)
            for q in alive:
                try:
                    q.kill()
                except Exception:
                    pass
            psutil.wait_procs(alive, timeout=3)
        except Exception:
            pass
    else:
        winproc.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=30)
    return [x for x in pids if _pid_alive(x)]


def wait_free(port, timeout=15.0):
    """等端口真的放掉（进程退了端口不一定立刻空）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not port_listening(port):
            return True
        time.sleep(0.2)
    return not port_listening(port)
