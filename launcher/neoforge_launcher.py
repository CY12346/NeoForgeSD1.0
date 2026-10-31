# -*- coding: utf-8 -*-
"""NeoForgeSD 1.3.0 —— 启动器（壳）。

壳归壳，核归核：
  - 壳不认识任何出图后端，只认 kernels/<名字>/kernel.json
  - 起核、判活、报进度、收尸，全走 core/pipe.py 的管道协议
  - 环境侦探 core/detect.py 把「缺什么、怎么补」说成人话

包体 <10MB，不含任何后端与模型权重。

打包：pyinstaller NeoForgeSD.spec
"""
import json
import os
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, scrolledtext, ttk

APP_NAME = "NeoForgeSD"
VERSION = "1.3.0"

# ---------------------------------------------------------------- 路径
if getattr(sys, "frozen", False):
    HERE = os.path.dirname(os.path.abspath(sys.executable))
else:
    HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 便携包：exe 同级有 backend/ 或 models/；开发仓库：脚本在 app/ 下
if os.path.isdir(os.path.join(HERE, "backend")) or os.path.isdir(os.path.join(HERE, "models")):
    BASE_DIR = HERE
else:
    BASE_DIR = os.path.dirname(HERE) if os.path.basename(HERE) == "app" else HERE

WORK_DIR = os.path.join(BASE_DIR, "work")
LOG_FILE = os.path.join(WORK_DIR, "launcher.log")

sys.path.insert(0, BASE_DIR)
for extra in (os.path.join(BASE_DIR, "app"), HERE):
    if os.path.isdir(extra) and extra not in sys.path:
        sys.path.insert(0, extra)

try:
    from core import detect, kernel, pipe
except ImportError as e:  # 壳缺件要说人话，不能黑框闪退
    print(f"启动器缺少 core 模块：{e}")
    raise

LEVEL_ICON = {detect.GREEN: "●", detect.YELLOW: "◐", detect.RED: "○"}
LEVEL_STYLE = {detect.GREEN: "Ok.TLabel", detect.YELLOW: "Warn.TLabel", detect.RED: "Bad.TLabel"}


def kernel_roots():
    """内核可能放两处：便携包根目录（exe 同级），或仓库里的 app/kernels。"""
    roots = []
    for r in (BASE_DIR, HERE):
        kd = os.path.join(r, "kernels")
        if os.path.isdir(kd) and kd not in [os.path.join(x, "kernels") for x in roots]:
            roots.append(r)
    return roots or [BASE_DIR]


def discover_all():
    """合并所有根目录下的内核；同 id 以先找到的为准。坏件单独列出。"""
    out, bad, seen = {}, [], set()
    for root in kernel_roots():
        for m, err in kernel.discover(root, base_dir=BASE_DIR):
            if m:
                if m["id"] not in seen:
                    seen.add(m["id"])
                    out[m["id"]] = m
            else:
                bad.append(err)
    return out, bad


STATE_TEXT = {
    pipe.IDLE: "— 未启动",
    pipe.STARTING: "◐ 启动中…",
    pipe.GREEN: "● 运行中",
    pipe.YELLOW: "◐ 运行中（健康检查异常）",
    pipe.STOPPED: "— 已停止",
    pipe.FAILED: "○ 出错",
}


class Launcher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {VERSION}")
        self.geometry("820x640")
        self.minsize(720, 560)
        self.configure(bg="#12141a")
        self.runner = None
        self.busy = False
        self.manifests = {}
        self.bad = []
        self._build_ui()
        self.after(100, self.reload_kernels)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background="#12141a")
        style.configure("Card.TFrame", background="#1a1d26", relief="flat")
        style.configure("TLabel", background="#12141a", foreground="#e6e8ef",
                        font=("Microsoft YaHei UI", 10))
        style.configure("Card.TLabel", background="#1a1d26", foreground="#e6e8ef",
                        font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", background="#12141a", foreground="#ffffff",
                        font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("Sub.TLabel", background="#12141a", foreground="#8b93a7",
                        font=("Microsoft YaHei UI", 9))
        style.configure("Ok.TLabel", background="#1a1d26", foreground="#4ade80",
                        font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Warn.TLabel", background="#1a1d26", foreground="#fbbf24",
                        font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Bad.TLabel", background="#1a1d26", foreground="#f87171",
                        font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Run.TButton", font=("Microsoft YaHei UI", 12, "bold"), padding=(18, 10))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(12, 7))
        style.configure("TCombobox", padding=4)

        head = ttk.Frame(self, padding=(22, 16, 22, 4))
        head.pack(fill="x")
        ttk.Label(head, text=APP_NAME, style="Title.TLabel").pack(side="left")
        ttk.Label(head, text=f"  v{VERSION} · 壳归壳，核归核",
                  style="Sub.TLabel").pack(side="left", pady=(9, 0))

        krow = ttk.Frame(self, padding=(22, 6))
        krow.pack(fill="x")
        ttk.Label(krow, text="内核", width=6).pack(side="left")
        self.cmb = ttk.Combobox(krow, state="readonly", width=38)
        self.cmb.pack(side="left")
        self.cmb.bind("<<ComboboxSelected>>", lambda _e: self.refresh_detail())
        self.lbl_state = ttk.Label(krow, text="—", style="Card.TLabel")
        self.lbl_state.pack(side="left", padx=12)

        card = ttk.Frame(self, style="Card.TFrame", padding=(16, 12))
        card.pack(fill="x", padx=22, pady=(8, 6))
        self.rows = {}
        for key in ("python", "models", "port", "disk", "gpu", "kernels"):
            row = ttk.Frame(card, style="Card.TFrame")
            row.pack(fill="x", pady=2)
            ttk.Label(row, text="", style="Card.TLabel", width=11).pack(side="left")
            val = ttk.Label(row, text="检测中…", style="Card.TLabel")
            val.pack(side="left", fill="x", expand=True)
            self.rows[key] = (row.winfo_children()[0], val)

        btns = ttk.Frame(self, padding=(22, 2))
        btns.pack(fill="x")
        self.btn_start = ttk.Button(btns, text="▶  启动", style="Run.TButton", command=self.on_start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(btns, text="■  停止", command=self.on_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=8)
        self.btn_open = ttk.Button(btns, text="打开页面", command=self.on_open)
        self.btn_open.pack(side="left")
        self.btn_check = ttk.Button(btns, text="重新检测", command=self.refresh_detect)
        self.btn_check.pack(side="left", padx=8)

        self.bar = ttk.Progressbar(self, mode="determinate", maximum=1.0)
        self.bar.pack(fill="x", padx=22, pady=(8, 2))

        logbox = ttk.Frame(self, padding=(22, 6, 22, 14))
        logbox.pack(fill="both", expand=True)
        self.log = scrolledtext.ScrolledText(
            logbox, height=12, bg="#0d0f14", fg="#c9d1e0", insertbackground="#c9d1e0",
            relief="flat", font=("Consolas", 9), wrap="word")
        self.log.pack(fill="both", expand=True)

    def logline(self, msg):
        import time
        ts = time.strftime("%H:%M:%S")
        self.log.insert("end", f"[{ts}] {msg}\n")
        self.log.see("end")
        try:
            os.makedirs(WORK_DIR, exist_ok=True)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {msg}\n")
        except OSError:
            pass

    # ------------------------------------------------------------- 内核登记册
    def reload_kernels(self):
        self.manifests, self.bad = discover_all()
        for err in self.bad:
            self.logline(f"✗ 内核登记失败：{err}")
        if not self.manifests:
            self.logline("✗ kernels/ 下没有可用内核，先放一个 kernel.json")
            self.cmb["values"] = []
            self.refresh_detect()
            return
        ids = list(self.manifests)
        self.cmb["values"] = [f"{i} · {self.manifests[i]['name']}" for i in ids]
        if not self.cmb.get():
            self.cmb.current(0)
        self.logline(f"内核登记册：{len(ids)} 个（{', '.join(ids)}）")
        self.refresh_detect()

    def current_manifest(self):
        v = self.cmb.get()
        if not v:
            return None
        return self.manifests.get(v.split(" · ")[0])

    # ---------------------------------------------------------------- 侦探
    def refresh_detect(self):
        def work():
            items = detect.run_checks(BASE_DIR)
            self.after(0, lambda: self._apply(items))
        threading.Thread(target=work, daemon=True).start()

    def _apply(self, items):
        for it in items:
            pair = self.rows.get(it["key"])
            if not pair:
                continue
            label, val = pair
            label.configure(text=it["label"])
            icon = LEVEL_ICON.get(it["level"], "·")
            text = f"{icon} {it['detail']}"
            val.configure(text=text, style=LEVEL_STYLE.get(it["level"], "Card.TLabel"))
        for it in items:
            if it["level"] != detect.GREEN and it["fix"]:
                self.logline(f"{LEVEL_ICON[it['level']]} {it['label']}：{it['fix']}")
        level, summary = detect.verdict(items)
        m = self.current_manifest()
        if m and not self.busy:
            self.lbl_state.configure(text=f"· 就绪条件：{summary}", style="Card.TLabel")

    def refresh_detail(self):
        m = self.current_manifest()
        if not m:
            return
        self.logline(f"选中内核 {m['id']}：{m['command'][0]} … 端口 {m['port']} 健康 {m['health']['path']}")

    # ---------------------------------------------------------------- 起停
    def on_start(self):
        if self.busy:
            return
        m = self.current_manifest()
        if not m:
            messagebox.showerror(APP_NAME, "没有可启动的内核。\n请检查 kernels/ 下的 kernel.json。")
            return
        self.busy = True
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.bar.configure(value=0)
        self.logline(f"启动内核 {m['id']} · 命令 {' '.join(m['command'])}")
        threading.Thread(target=self._boot, args=(m,), daemon=True).start()

    def _boot(self, m):
        self.runner = pipe.KernelRunner(
            m,
            log=lambda msg: self.after(0, lambda: self.logline(msg)),
            on_progress=lambda f, t: self.after(0, lambda: self._progress(f, t)),
            on_state=lambda s, d: self.after(0, lambda: self._state(s, d)),
        )
        ok = self.runner.start(wait_ready=300)
        self.busy = False
        if ok:
            self.after(0, self.on_open)
        else:
            self.after(0, lambda: self.logline(f"✗ 起不来：{self.runner.last_error}"))
        self.after(0, lambda: self.btn_start.configure(
            state="normal" if not ok or not self._running() else "disabled"))

    def _running(self):
        return bool(self.runner and self.runner.proc and self.runner.proc.poll() is None)

    def _progress(self, frac, text):
        self.bar.configure(value=frac)
        self.title(f"{APP_NAME} {VERSION} · {frac:.0%} {text}")

    def _state(self, state, detail):
        self.lbl_state.configure(text=STATE_TEXT.get(state, state), style="Card.TLabel")
        if state == pipe.GREEN:
            self.title(f"{APP_NAME} {VERSION}")
            self.btn_start.configure(state="disabled")
            self.btn_stop.configure(state="normal")

    def on_stop(self):
        if not self.runner:
            return
        self.btn_stop.configure(state="disabled")
        self.logline("正在停止…")

        def work():
            clean, alive = self.runner.stop()
            self.after(0, lambda: self.logline("已停止：端口释放，无孤儿" if clean
                                               else f"✗ 停止不干净，残留 {alive}"))
            self.after(0, lambda: self.btn_start.configure(state="normal"))
            self.after(0, lambda: self.bar.configure(value=0))
            self.after(0, lambda: self.title(f"{APP_NAME} {VERSION}"))
        threading.Thread(target=work, daemon=True).start()

    def on_open(self):
        m = self.current_manifest()
        port = m["port"] if m else 8899
        webbrowser.open(f"http://127.0.0.1:{port}/")


def main():
    os.makedirs(WORK_DIR, exist_ok=True)
    app = Launcher()
    app.logline(f"{APP_NAME} {VERSION} 启动器已就绪（壳不含后端）")
    app.logline(f"根目录：{BASE_DIR}")
    app.mainloop()


if __name__ == "__main__":
    main()
