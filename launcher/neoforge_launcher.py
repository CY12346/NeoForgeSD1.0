# -*- coding: utf-8 -*-
"""NeoForgeSD 图形启动器 —— 双击即用，不出现黑框。

它做的事：
  1. 自检：后端 / 前端 / 模型 是否就位（后端自带 python，不依赖系统环境）
  2. 启动：用后端的 python 跑 ui/image_app.py（它自己会拉起 SD 后端）
  3. 就绪后自动打开浏览器
  4. 一键停止全部服务

打包：pyinstaller --onefile --windowed --name NeoForgeSD launcher/neoforge_launcher.py
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
import webbrowser
from tkinter import messagebox, scrolledtext, ttk

APP_NAME = "NeoForgeSD"
VERSION = "1.2.2"
PORT = 8899

# exe 所在目录（PyInstaller onefile 解包到临时目录，所以用 exe 的真实路径）
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BACKEND = os.path.join(BASE_DIR, "backend")
APP_DIR = os.path.join(BASE_DIR, "app")
MODELS = os.path.join(BASE_DIR, "models")
FRONTEND = os.path.join(APP_DIR, "ui", "image_app.py")
BACKEND_PY = os.path.join(BACKEND, "python", "python.exe")
WORK_DIR = os.path.join(BASE_DIR, "work")
LOG_FILE = os.path.join(WORK_DIR, "launcher.log")


def first_model():
    """返回 models/Stable-diffusion 下第一个模型路径（用于自检提示）。"""
    root = os.path.join(MODELS, "Stable-diffusion")
    if not os.path.isdir(root):
        return None
    for dirpath, _dirnames, filenames in os.walk(root):
        for f in filenames:
            if f.lower().endswith((".safetensors", ".ckpt")):
                return os.path.join(dirpath, f)
    return None


def ensure_models_link():
    """A1111 只认 backend/models；便携布局里模型放在同级 models/，首次运行时建目录联接。

    坑：A1111 自己在没有 models 时会建空壳目录（只有 Codeformer/GFPGAN 这些子目录），
    必须先识别出「空壳」再换成联接，否则模型永远不可见。
    """
    link = os.path.join(BACKEND, "models")
    if os.path.isdir(link):
        try:
            if set(os.listdir(link)) == set(os.listdir(MODELS)):
                return True                     # 已是联接或内容一致
        except OSError:
            return True
        try:
            os.rmdir(link)                      # 只删空目录；有内容就放弃，避免破坏
        except OSError:
            shutil.rmtree(link, ignore_errors=True)
            if os.path.isdir(link):
                return True
    if not os.path.isdir(MODELS):
        return False
    try:
        subprocess.run(["cmd", "/c", "mklink", "/J",
                        os.path.normpath(link), os.path.normpath(MODELS)],
                       capture_output=True, timeout=30,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return False
    return os.path.exists(link)


def api_status(timeout=3):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/status", timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def port_open(port=PORT):
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


class Launcher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {VERSION}")
        self.geometry("760x560")
        self.minsize(680, 500)
        self.configure(bg="#12141a")
        self.proc = None
        self.busy = False
        self._build_ui()
        self.after(200, self.refresh_all)

    # ---------------------------------------------------------------- UI
    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background="#12141a")
        style.configure("Card.TFrame", background="#1a1d26", relief="flat")
        style.configure("TLabel", background="#12141a", foreground="#e6e8ef", font=("Microsoft YaHei UI", 10))
        style.configure("Card.TLabel", background="#1a1d26", foreground="#e6e8ef", font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", background="#12141a", foreground="#ffffff", font=("Microsoft YaHei UI", 17, "bold"))
        style.configure("Sub.TLabel", background="#12141a", foreground="#8b93a7", font=("Microsoft YaHei UI", 9))
        style.configure("Ok.TLabel", background="#1a1d26", foreground="#4ade80", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Bad.TLabel", background="#1a1d26", foreground="#f87171", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Run.TButton", font=("Microsoft YaHei UI", 12, "bold"), padding=(18, 10))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(12, 7))

        head = ttk.Frame(self, padding=(22, 18, 22, 6))
        head.pack(fill="x")
        ttk.Label(head, text=f"{APP_NAME}", style="Title.TLabel").pack(side="left")
        ttk.Label(head, text=f"  v{VERSION} · 本地 AI 出图，双击即用", style="Sub.TLabel").pack(side="left", pady=(8, 0))

        card = ttk.Frame(self, style="Card.TFrame", padding=(18, 14))
        card.pack(fill="x", padx=22, pady=(6, 8))
        self.rows = {}
        for key, label in (("backend", "SD 后端"), ("model", "基础模型"), ("service", "服务状态")):
            row = ttk.Frame(card, style="Card.TFrame")
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=label, style="Card.TLabel", width=10).pack(side="left")
            val = ttk.Label(row, text="检测中…", style="Card.TLabel")
            val.pack(side="left", fill="x", expand=True)
            self.rows[key] = val

        btns = ttk.Frame(self, padding=(22, 4))
        btns.pack(fill="x")
        self.btn_start = ttk.Button(btns, text="▶  启动", style="Run.TButton", command=self.on_start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(btns, text="■  停止", command=self.on_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=8)
        self.btn_open = ttk.Button(btns, text="打开页面", command=self.on_open)
        self.btn_open.pack(side="left")
        self.btn_check = ttk.Button(btns, text="重新检测", command=self.refresh_all)
        self.btn_check.pack(side="left", padx=8)

        logbox = ttk.Frame(self, padding=(22, 10, 22, 16))
        logbox.pack(fill="both", expand=True)
        self.log = scrolledtext.ScrolledText(
            logbox, height=12, bg="#0d0f14", fg="#c9d1e0", insertbackground="#c9d1e0",
            relief="flat", font=("Consolas", 9), wrap="word",
        )
        self.log.pack(fill="both", expand=True)

    def logline(self, msg):
        ts = time.strftime("%H:%M:%S")
        self.log.insert("end", f"[{ts}] {msg}\n")
        self.log.see("end")
        try:
            os.makedirs(WORK_DIR, exist_ok=True)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {msg}\n")
        except OSError:
            pass

    # ------------------------------------------------------------ 自检
    def refresh_all(self):
        if self.busy:
            return
        ok_backend = os.path.isfile(BACKEND_PY)
        self.rows["backend"].configure(
            text=(f"✓ {BACKEND}" if ok_backend else f"✗ 缺少 {BACKEND_PY}"),
            style=("Ok.TLabel" if ok_backend else "Bad.TLabel"))

        m = first_model()
        self.rows["model"].configure(
            text=(f"✓ {os.path.basename(m)}" if m else "✗ 未找到 .safetensors（放到 models/Stable-diffusion/）"),
            style=("Ok.TLabel" if m else "Bad.TLabel"))

        st = api_status()
        if st and st.get("online"):
            self.rows["service"].configure(text=f"✓ 运行中 · 模型 {st.get('model')}", style="Ok.TLabel")
            self.btn_stop.configure(state="normal")
            self.btn_start.configure(state="disabled")
        elif port_open():
            self.rows["service"].configure(text="◐ 启动中…（首次加载模型约 60–120 秒）", style="Card.TLabel")
            self.btn_stop.configure(state="normal")
            self.btn_start.configure(state="disabled")
        else:
            self.rows["service"].configure(text="— 未启动", style="Card.TLabel")
            self.btn_stop.configure(state="disabled")
            self.btn_start.configure(state="normal" if ok_backend else "disabled")

        if not self.busy:
            self.after(3000, self.refresh_all)

    # ------------------------------------------------------------ 启动
    def on_start(self):
        if self.busy:
            return
        if not os.path.isfile(BACKEND_PY):
            messagebox.showerror(APP_NAME, f"找不到 SD 后端：\n{BACKEND_PY}\n\n请确认 backend 目录与启动器在同一个文件夹下。")
            return
        if not os.path.isfile(FRONTEND):
            messagebox.showerror(APP_NAME, f"找不到前端：\n{FRONTEND}")
            return
        if not ensure_models_link():
            self.logline("⚠ 模型目录未连接（backend/models），请确认 models/ 与启动器同级")
        elif not os.path.exists(os.path.join(WORK, "models_linked.flag")):
            self.logline("模型目录已连接：backend/models → models")
            try:
                os.makedirs(WORK, exist_ok=True)
                open(os.path.join(WORK, "models_linked.flag"), "w").close()
            except Exception:
                pass
        if port_open():
            self.logline("服务已在运行，直接打开页面")
            self.on_open()
            return
        self.busy = True
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.logline("正在启动前端（后端由它自动拉起）…")
        threading.Thread(target=self._boot, daemon=True).start()

    def _boot(self):
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", NEOFORGE_IMAGE_PORT=str(PORT),
                   NEOFORGE_SD_HOME=str(BACKEND), NEOFORGE_IMAGE_WORK=str(WORK))
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.proc = subprocess.Popen(
                [BACKEND_PY, FRONTEND], cwd=APP_DIR, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=creation, text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
        except Exception as e:
            self.logline(f"启动失败：{e}")
            self.busy = False
            self.btn_start.configure(state="normal")
            return

        for _ in range(240):                       # 最多 8 分钟
            time.sleep(1)
            if api_status(2):
                self.logline("服务已就绪，正在打开浏览器")
                self.busy = False
                self.on_open()
                return
            if self.proc.poll() is not None:
                self.logline(f"进程退出（码 {self.proc.returncode}）")
                break
        self.logline("等待超时，请查看日志")
        self.busy = False

    def on_stop(self):
        self.logline("正在停止…")
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass
            self.proc = None
        # 兜底：按端口收尸
        try:
            out = subprocess.run(["netstat", "-ano"], capture_output=True, timeout=15).stdout
            for line in out.decode("gbk", "replace").splitlines():
                if f":{PORT}" in line and "LISTENING" in line.upper():
                    pid = line.split()[-1].strip()
                    if pid.isdigit():
                        subprocess.run(["taskkill", "/PID", pid, "/F", "/T"], capture_output=True, timeout=15)
                        self.logline(f"已结束进程 {pid}")
        except Exception as e:
            self.logline(f"停止时出错：{e}")
        self.after(800, self.refresh_all)

    def on_open(self):
        webbrowser.open(f"http://127.0.0.1:{PORT}/")


def main():
    os.makedirs(WORK_DIR, exist_ok=True)
    app = Launcher()
    app.logline(f"{APP_NAME} {VERSION} 启动器已就绪")
    app.logline(f"目录：{BASE_DIR}")
    app.mainloop()


if __name__ == "__main__":
    main()
