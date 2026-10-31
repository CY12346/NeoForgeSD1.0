# -*- coding: utf-8 -*-
"""内核管道：起核 · 判活 · 进度 · 收尸 · 不留孤儿。

壳只通过这个模块碰内核，内核的怪癖全写在它自己的 kernel.json 里。
"""
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

try:
    import psutil
except ImportError:  # 打包后可能没有，退到 taskkill
    psutil = None

IDLE, STARTING, GREEN, YELLOW, STOPPED, FAILED = (
    "idle", "starting", "green", "yellow", "stopped", "failed")


class KernelRunner:
    """一个核的完整生命周期。线程安全靠一把锁 + 明确的停止标志。"""

    def __init__(self, manifest, log=None, on_progress=None, on_state=None):
        self.m = manifest
        self.log = log or (lambda msg: None)
        self.on_progress = on_progress or (lambda frac, text: None)
        self.on_state = on_state or (lambda state, detail: None)
        self.proc = None
        self.state = IDLE
        self.last_error = ""
        self._stop_flag = threading.Event()
        self._ready_evt = threading.Event()
        self._threads = []
        self._children = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 状态
    def set_state(self, state, detail=""):
        with self._lock:
            self.state = state
        try:
            self.on_state(state, detail)
        except Exception:
            pass

    def port_free(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            return s.connect_ex(("127.0.0.1", self.m["port"])) != 0

    def port_open(self):
        return not self.port_free()

    # ------------------------------------------------------------ 判活
    def health_once(self):
        h = self.m["health"]
        url = f"http://127.0.0.1:{self.m['port']}{h['path']}"
        try:
            with urllib.request.urlopen(url, timeout=h["timeout_s"]) as r:
                return 200 <= r.status < 400
        except urllib.error.HTTPError as e:
            return 200 <= e.code < 400
        except Exception:
            return False

    # ------------------------------------------------------------ 起
    def build_cmd(self):
        cmd = list(self.m["command"]) + list(self.m.get("args") or [])
        return cmd

    def start(self, wait_ready=None):
        """起核。返回 True = 已就绪；False = 起不来（原因在 last_error / 日志里）。"""
        if self.proc and self.proc.poll() is None:
            self.log("内核已在运行，忽略重复启动")
            return True

        cmd = self.build_cmd()
        cwd = self.m["cwd"]
        if not os.path.isdir(cwd):
            self.last_error = f"工作目录不存在：{cwd}"
            self.set_state(FAILED, self.last_error)
            return False

        env = dict(os.environ)
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.update({k: str(v) for k, v in (self.m.get("env") or {}).items()})

        self._stop_flag.clear()
        self._ready_evt.clear()
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=cwd, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=creation,
            )
        except FileNotFoundError:
            self.last_error = f"启动命令里的程序不存在：{cmd[0]}\n照 SPEC-kernel.md 检查 command 与 {python_hint()}"
            self.set_state(FAILED, self.last_error)
            return False
        except Exception as e:
            self.last_error = f"启动失败：{e}"
            self.set_state(FAILED, self.last_error)
            return False

        self.set_state(STARTING, f"pid={self.proc.pid}")
        self.log(f"已启动 {self.m['id']} · pid={self.proc.pid} · 端口 {self.m['port']}")

        t = threading.Thread(target=self._reader, daemon=True)
        t.start()
        self._threads = [t]

        timeout = wait_ready if wait_ready is not None else 180
        mode = self.m["ready"]["mode"]
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                self.last_error = f"进程提前退出（码 {self.proc.returncode}）"
                self.set_state(FAILED, self.last_error)
                return False
            if self._ready_evt.is_set() and mode in ("line", "both"):
                break
            if mode in ("poll", "both") and self.health_once():
                self._ready_evt.set()
                break
            time.sleep(0.3)

        if not self._ready_evt.is_set():
            self.last_error = f"等待就绪超时（{timeout}s）"
            self.set_state(YELLOW, self.last_error)
            return False

        self.set_state(GREEN, "就绪")
        self.log(f"就绪：{self.m['id']} → http://127.0.0.1:{self.m['port']}{self.m['health']['path']}")
        h = threading.Thread(target=self._health_loop, daemon=True)
        h.start()
        self._threads.append(h)
        return True

    # ------------------------------------------------------------ 读 stdout
    def _reader(self):
        ready_re = self.m["ready"]["regex"]
        prog_re = self.m["progress"]["regex"]
        for line in iter(self.proc.stdout.readline, ""):
            s = line.rstrip("\r\n")
            if not s:
                continue
            if prog_re.search(s):
                g = prog_re.search(s).groups()
                try:
                    frac = float(g[0])
                except (TypeError, ValueError):
                    frac = 0.0
                text = (g[1] if len(g) > 1 else "") or ""
                self.on_progress(frac, text)
                self.log(f"[进度 {frac:.0%}] {text}")
                continue
            if s.startswith("PIPE:LOG"):
                self.log(s[len("PIPE:LOG"):].strip())
                continue
            if not self._ready_evt.is_set() and ready_re.search(s):
                self._ready_evt.set()
                self.log(f"收到握手：{s}")
                continue
            self.log(s)
        # 进程结束
        rc = self.proc.poll() if self.proc else None
        if not self._stop_flag.is_set():
            self.last_error = f"内核自己退出了（码 {rc}）"
            self.set_state(FAILED, self.last_error)
        self._ready_evt.clear()

    # ------------------------------------------------------------ 判活循环
    def _health_loop(self):
        h = self.m["health"]
        fails = 0
        while self.proc and self.proc.poll() is None and not self._stop_flag.is_set():
            time.sleep(h["interval_s"])
            if self.health_once():
                if fails >= h["fail_threshold"]:
                    self.log("健康检查恢复")
                    self.set_state(GREEN, "就绪")
                fails = 0
                continue
            fails += 1
            if fails == h["fail_threshold"]:
                self.set_state(YELLOW, f"健康检查连续 {fails} 次失败：{h['path']}")
                self.log(f"⚠ 连续 {fails} 次健康检查失败（进程还在，但没响应）")

    # ------------------------------------------------------------ 收尸
    def _collect_pids(self):
        """拿整棵进程树（父 + 所有后代）。"""
        if not self.proc:
            return []
        pids = [self.proc.pid]
        if psutil:
            try:
                p = psutil.Process(self.proc.pid)
                self._children = [c.pid for c in p.children(recursive=True)]
                pids += self._children
            except Exception:
                pass
        return pids

    def stop(self, grace_s=None):
        """温和 → 强杀 → 验证。返回 (是否干净, 残留 pid 列表)。"""
        if not self.proc:
            self.set_state(STOPPED, "未在运行")
            return True, []
        grace = grace_s if grace_s is not None else self.m["stop"]["grace_s"]
        self._stop_flag.set()
        pids = self._collect_pids()
        self.log(f"正在停止 {self.m['id']}（进程树 {pids}）")

        self.proc.terminate()
        deadline = time.time() + grace
        while time.time() < deadline and self.proc.poll() is None:
            time.sleep(0.2)

        if self.proc.poll() is None:
            self.log(f"温和终止 {grace}s 未退出，强杀整棵进程树")
            if psutil:
                self._kill_tree_psutil()
            else:
                subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                               capture_output=True, timeout=20)
        else:
            # 父进程退了，但子进程可能还在（A1111 前后端就是这种）
            leftovers = [p for p in pids if p != self.proc.pid and _alive(p)]
            if leftovers:
                self.log(f"父进程已退，仍有子进程存活：{leftovers} → 一并清掉")
                if psutil:
                    self._kill_tree_psutil()
                else:
                    subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                                   capture_output=True, timeout=20)

        for t in self._threads:
            t.join(timeout=3)
        self._threads = []

        alive = [p for p in pids if _alive(p)]
        clean = not alive and self.port_free()
        self.proc = None
        self._ready_evt.clear()
        if clean:
            self.set_state(STOPPED, "已停止，端口已释放，无孤儿")
            self.log("已停止：端口释放，无残留进程")
        else:
            self.set_state(FAILED, f"停止不干净：残留 {alive}，端口{'仍占用' if not self.port_free() else '已释放'}")
            self.log(f"✗ 停止不干净：残留 pid={alive}")
        return clean, alive

    def _kill_tree_psutil(self):
        try:
            p = psutil.Process(self.proc.pid)
            procs = p.children(recursive=True) + [p]
        except Exception:
            procs = []
        for q in procs:
            try:
                q.terminate()
            except Exception:
                pass
        _, alive = psutil.wait_procs(procs, timeout=3)
        for q in alive:
            try:
                q.kill()
            except Exception:
                pass
        psutil.wait_procs(alive, timeout=3)


def _alive(pid):
    if psutil:
        try:
            p = psutil.Process(pid)
            return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
        except Exception:
            return False
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                             capture_output=True, timeout=10).stdout.decode("gbk", "replace")
        return str(pid) in out
    except Exception:
        return False


def python_hint():
    import sys
    return f"当前解释器：{sys.executable}"
