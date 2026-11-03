# -*- coding: utf-8 -*-
"""NeoForgeSD 1.3.0 —— 启动器（壳）。

壳归壳，核归核：
  - 壳不认识任何出图后端，只认 kernels/<名字>/kernel.json
  - 起核、判活、报进度、收尸，全走 core/pipe.py 的管道协议
  - 环境侦探 core/detect.py 把「缺什么、怎么补」说成人话

界面：sv-ttk 打底（Win11 Fluent，+50KB），品牌橙只用在自绘元素上
（启动键、状态点、选中行、进度条），因为 sv-ttk 的按钮强调色是预渲染雪碧图，改不动。

包体 <10MB，不含任何后端与模型权重。

打包：pyinstaller NeoForgeSD.spec
"""
import datetime
import hashlib
import json
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

APP_NAME = "NeoForgeSD"
VERSION = "1.4.2"
LAUNCHER_VERSION = "0.3"            # 启动器自己的版本线（0.2→0.3：在线模型目录 + 双内核 + 热度榜）
INSTALLER_LABEL = f"启动器 {LAUNCHER_VERSION}"

ACCENT = "#ff7a2f"          # 锻造橙
ACCENT_HOVER = "#ff9457"
INK = "#141414"             # 橙底上的字

# ---------------------------------------------------------------- 路径
if getattr(sys, "frozen", False):
    HERE = os.path.dirname(os.path.abspath(sys.executable))
else:
    HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 便携包：exe 同级有 backend/ 或 models/；开发仓库：脚本在 app/ 下
def _find_base(start):
    """从启动位置往上找真正的安装根。

    frozen 后 exe 往往在 <根>/app/dist/ 里：直接拿 dist 当根，就会
    找不到 backend/、work/ 落到 dist/work 下，还会误判成「第一次用」弹引导页。
    """
    cur = start
    for _ in range(4):
        if os.path.isdir(os.path.join(cur, "backend")) or os.path.isdir(os.path.join(cur, "models")):
            return cur
        if os.path.basename(cur).lower() == "app":
            return os.path.dirname(cur)
        if os.path.isfile(os.path.join(cur, "app", "launcher", "neoforge_launcher.py")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return start


BASE_DIR = _find_base(HERE)

WORK_DIR = os.path.join(BASE_DIR, "work")
LOG_FILE = os.path.join(WORK_DIR, "launcher.log")
PACK_DIR = os.path.join(WORK_DIR, "pack")
SETUP_FILE = os.path.join(WORK_DIR, "setup.json")      # 首启引导做过的凭证
DOWNLOAD_DIR = os.path.join(WORK_DIR, "downloads")
SILENT = os.environ.get("NEOFORGE_SILENT") == "1"   # 脚本/自测：只记日志，不弹窗


ZWSP = "​"          # 零宽空格：给 Tk 一个可断行的位置


def hard_wrap(text, width=46):
    """自己在字符串里排好行（插真换行）。

    Tk 的 wraplength 只在空格处断行，中文一整段被当成一个"单词"，
    结果就是「WebUI」孤零零占一行、后面一大坨挤到下一行；插零宽空格
    Tk 8.6 又不认。所以干脆自己按显示宽度贪心排：英文单词整体不拆，
    汉字每字 2 个单位（约等于两个西文字符宽）。
    """
    s = " ".join(str(text).split())
    if not s:
        return ""

    def unit(ch):
        return 2 if ord(ch) > 0x2E7F else 1

    ALNUM = set("_./+-%")
    out, cur, used, i, n = [], "", 0, 0, len(s)
    while i < n:
        ch = s[i]
        if ch.isascii() and (ch.isalnum() or ch in ALNUM):
            j = i
            while j < n and s[j].isascii() and (s[j].isalnum() or s[j] in ALNUM):
                j += 1
            tok = s[i:j]
        else:
            tok, j = ch, i + 1
        tu = sum(unit(c) for c in tok)
        if used + tu > width and cur.strip():
            out.append(cur.rstrip())
            cur, used = "", 0
            if tok == " ":
                i = j
                continue
        if tu > width:                       # 超长 token 只能硬切
            for c in tok:
                if used + unit(c) > width and cur.strip():
                    out.append(cur.rstrip())
                    cur, used = "", 0
                cur += c
                used += unit(c)
            i = j
            continue
        cur += tok
        used += tu
        i = j
    if cur.strip():
        out.append(cur.rstrip())
    return "\n".join(out)


def read_setup():
    try:
        with open(SETUP_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_setup(ui, **extra):
    """记下「这台机器已经做过选择」—— 拖整合包装成功的也一样，下次不再问。"""
    os.makedirs(WORK_DIR, exist_ok=True)
    data = {"ui": ui, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    data.update(extra)
    tmp = SETUP_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, SETUP_FILE)
    return data


def has_backend(base=None):
    """后端 Python 在不在 —— 在就说明这台机器不是第一次用，别拿引导页糊人脸。"""
    return os.path.isfile(os.path.join(base or BASE_DIR, "backend", "python", "python.exe"))

sys.path.insert(0, BASE_DIR)
for extra in (os.path.join(BASE_DIR, "app"), HERE):
    if os.path.isdir(extra) and extra not in sys.path:
        sys.path.insert(0, extra)

try:
    from core import detect, droptarget, installer, kernel, pipe, repair, selfcheck, singleton, winproc
    from core import catalog as cat
    from core import downloader
    from core import msremote
    from core import pack as packer
    from core import popularity as pop_util
except ImportError as e:  # 壳缺件要说人话，不能黑框闪退
    print(f"启动器缺少 core 模块：{e}")
    raise

try:                        # 主题库可缺省：没有就退回 ttk 手配样式，功能不受影响
    import sv_ttk
    HAS_SVTTK = True
except ImportError:
    HAS_SVTTK = False

LEVEL_ICON = {detect.GREEN: "●", detect.YELLOW: "◐", detect.RED: "○"}
LEVEL_COLOR = {detect.GREEN: "#3ddc84", detect.YELLOW: "#ffc53d", detect.RED: "#ff5c5c"}

PALETTE = {"bg": "#1c1c1c", "fg": "#ffffff", "sub": "#9aa3b2", "panel": "#12161d"}


def kernel_roots():
    """内核可能放三处：便携包根目录（exe 同级，用户可自己塞）、仓库里的 app/kernels、
    以及打包时随 exe 压进 _MEIPASS 的那份（便携包双击就是靠它，别漏）。"""
    candidates = [BASE_DIR, HERE]
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        candidates.append(meipass)
    roots, seen = [], set()
    for r in candidates:
        kd = os.path.join(r, "kernels")
        if os.path.isdir(kd) and kd not in seen:
            seen.add(kd)
            roots.append(r)
    return roots or [BASE_DIR]


def discover_all():
    """合并所有根目录下的内核；同 id 以先找到的为准。坏件单独列出。"""
    out, bad, seen = {}, [], set()
    for root in kernel_roots():
        for m, err in kernel.discover(root, base_dir=BASE_DIR):
            if m:
                if m.get("hidden"):      # 自测用的假内核之类：登记但不上界面
                    continue
                if m["id"] not in seen:
                    seen.add(m["id"])
                    out[m["id"]] = m
            else:
                bad.append(err)
    return out, bad


def display_name(m):
    """id 已经念过一遍就别再念第二遍（原来 a1111 · A1111 · NeoForgeSD 前端）。"""
    name = m.get("name", m["id"])
    return name if m["id"].lower() in name.lower() else f"{m['id']} · {name}"


STATE_TEXT = {
    pipe.IDLE: "未启动",
    pipe.STARTING: "启动中…",
    pipe.GREEN: "运行中",
    pipe.YELLOW: "运行中（健康检查异常）",
    pipe.STOPPED: "已停止",
    pipe.FAILED: "出错",
}

# ---------------------------------------------------------------- 图库
IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
GALLERY_LIMIT = 200        # 一次最多列 200 张（再多翻不动，先管最近这批）
THUMB_PX = 156             # 缩略图长边
GALLERY_COLS = 5
THUMB_DIR = os.path.join(WORK_DIR, "thumbs")

_PIL = "unloaded"


def _pil():
    """PIL 是可选的：壳不带它（包要小），有就出高质量缩略图，没有就靠 Tk 自己读 PNG。"""
    global _PIL
    if _PIL == "unloaded":
        try:
            from PIL import Image
            _PIL = Image
        except Exception:
            _PIL = None
    return _PIL


def scan_gallery(outputs_dir, limit=GALLERY_LIMIT):
    """递归扫出图目录里的图片，按修改时间倒序，取最近 limit 张。

    返回 (items, total)：items 每项 {path, mtime, size, thumb}，total 是实际张数。
    """
    items, total = [], 0
    for root, dirs, files in os.walk(outputs_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in files:
            if not fn.lower().endswith(IMG_EXTS):
                continue
            fp = os.path.join(root, fn)
            try:
                mt = os.path.getmtime(fp)
                sz = os.path.getsize(fp)
            except OSError:
                continue
            total += 1
            items.append({"path": fp, "mtime": mt, "size": sz, "thumb": None})
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items[:limit], total


def _thumb_file(src, mtime, px=THUMB_PX):
    key = hashlib.md5(f"{src}|{int(mtime)}|{px}".encode("utf-8")).hexdigest()
    return os.path.join(THUMB_DIR, f"{key}_{px}.png")


def make_thumbs(items, px=THUMB_PX):
    """后台线程里跑：给每项补 it['thumb']（生成的缩略图文件路径，失败为 None）。

    缩略图落 work/thumbs/ 缓存，同一张图（路径+时间戳）只做一次。
    """
    Image = _pil()
    if Image is None:
        return
    try:
        os.makedirs(THUMB_DIR, exist_ok=True)
    except OSError:
        return
    for it in items:
        tp = _thumb_file(it["path"], it["mtime"], px)
        if os.path.isfile(tp):
            it["thumb"] = tp
            continue
        try:
            with Image.open(it["path"]) as im:
                im.thumbnail((px, px), Image.LANCZOS)
                im.save(tp, "PNG")
            it["thumb"] = tp
        except Exception:
            it["thumb"] = None


def _day_label(day):
    """今天 / 昨天 / 具体日期 —— 组标题。"""
    today = datetime.date.today()
    if day == today:
        head = "今天"
    elif day == today - datetime.timedelta(days=1):
        head = "昨天"
    elif day == today - datetime.timedelta(days=2):
        head = "前天"
    else:
        head = ""
    stamp = f"{day.month}月{day.day}日"
    if day.year != today.year:
        stamp = f"{day.year}-{stamp}"
    return f"{head} · {stamp}" if head else stamp


class Gauge(tk.Canvas):
    """自绘橙色进度条：sv-ttk 的进度条是雪碧图，改不了色，这只自己画。"""

    def __init__(self, parent, bg, height=10, fg=ACCENT, track="#232a36"):
        super().__init__(parent, height=height, bg=bg, highlightthickness=0, bd=0)
        self._v = 0.0
        self._fg, self._track = fg, track
        self.bind("<Configure>", lambda _e: self._draw())

    def set(self, v):
        self._v = max(0.0, min(1.0, float(v)))
        self._draw()

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 2 or h <= 2:
            return
        r = h / 2.0
        self.create_oval(0, 0, h, h, fill=self._track, outline="")
        self.create_oval(w - h, 0, w, h, fill=self._track, outline="")
        self.create_rectangle(r, 0, w - r, h, fill=self._track, outline="")
        if self._v <= 0:
            return
        fw = max(h, w * self._v)
        self.create_oval(0, 0, h, h, fill=self._fg, outline="")
        self.create_oval(fw - h, 0, fw, h, fill=self._fg, outline="")
        self.create_rectangle(r, 0, max(r, fw - r), h, fill=self._fg, outline="")


class Launcher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {VERSION}")
        self.geometry("900x640+120+80")
        self.minsize(820, 580)
        self.runner = None
        self.busy = False
        self.manifests = {}
        self.ids = []
        self.bad = []
        self.gallery_visible = False
        self._gal_cells = []
        self._gal_photos = []
        self._apply_theme()
        self._build_ui()
        self._set_icon()
        self.guard = None                                   # 单实例守卫句柄（main() 里赋值）
        self.protocol("WM_DELETE_WINDOW", self.on_close)     # 关窗就得把核一起带走
        self.after(60, self._dark_titlebar)
        self.after(100, self.reload_kernels)
        self.after(200, self._enable_drop)                  # 拖放：装窗口过程要等真窗口出来
        self.after(1500, self._poll_drop_inbox)             # 别人（拖到 exe 上的那份）递进来的包
        self.after(500, self.maybe_first_run)               # 第一次用：先选一条出图路线

    # ------------------------------------------------------------------ 主题
    def _set_icon(self):
        for cand in (os.path.join(HERE, "assets", "neoforge.ico"),
                     os.path.join(BASE_DIR, "app", "assets", "neoforge.ico"),
                     os.path.join(getattr(sys, "_MEIPASS", ""), "assets", "neoforge.ico")):
            if cand and os.path.isfile(cand):
                try:
                    self.iconbitmap(cand)
                    return
                except tk.TclError:
                    pass

    def _dark_titlebar(self):
        """Win11 系统标题栏跟着变深，不然深色窗口顶着一条白条。"""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            on = ctypes.c_int(1)
            for attr in (20, 19):          # DWMWA_USE_IMMERSIVE_DARK_MODE（20/19 两个版本号都试）
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(on), ctypes.sizeof(on))
            cap = ctypes.c_int(0x001C1C1C)  # COLORREF: 0x00BBGGRR
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 35, ctypes.byref(cap), ctypes.sizeof(cap))
        except Exception:
            pass

    def _apply_theme(self):
        if HAS_SVTTK:
            sv_ttk.set_theme("dark")
        else:
            style = ttk.Style(self)
            try:
                style.theme_use("clam")
            except tk.TclError:
                pass
            style.configure("TFrame", background=PALETTE["bg"])
            style.configure("TLabel", background=PALETTE["bg"], foreground=PALETTE["fg"])
        self.configure(bg=PALETTE["bg"])
        # 从主题里读真实底色，别硬编码
        def look(widget, opt):
            try:
                return self.tk.call("ttk::style", "lookup", widget, "-" + opt)
            except tk.TclError:
                return ""
        self.P = {
            "bg": look("TFrame", "background") or PALETTE["bg"],
            "fg": look("TLabel", "foreground") or PALETTE["fg"],
            "sub": "#8b94a7",
            "sunken": "#0d1016",
        }

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        p = self.P
        font_ui = ("Microsoft YaHei UI", 10)
        font_bold = ("Microsoft YaHei UI", 10, "bold")
        font_small = ("Microsoft YaHei UI", 9)
        font_mono = ("Consolas", 9)

        # ---- 顶栏
        head = ttk.Frame(self, padding=(20, 14, 20, 4))
        head.pack(fill="x")
        brand = tk.Frame(head, bg=p["bg"])
        brand.pack(side="left")
        tk.Label(brand, text="⬢", bg=p["bg"], fg=ACCENT,
                 font=("Segoe UI Symbol", 20)).pack(side="left", padx=(0, 8))
        tk.Label(brand, text=APP_NAME, bg=p["bg"], fg=p["fg"],
                 font=("Microsoft YaHei UI", 18, "bold")).pack(side="left")
        tk.Label(brand, text=f"  v{VERSION} · {INSTALLER_LABEL} · 壳归壳，核归核", bg=p["bg"], fg=p["sub"],
                 font=font_small).pack(side="left", pady=(8, 0))
        self.lbl_state = tk.Label(head, text="· 检测中…", bg=p["bg"], fg=p["sub"],
                                  font=font_ui, anchor="e", justify="right", wraplength=430)
        self.lbl_state.pack(side="right", pady=(8, 0), fill="x")

        # 主界面装进 page_main，图库是它的兄弟页（点「图库」切过去，head 常驻）
        self.page_main = ttk.Frame(self)
        self.page_main.pack(fill="both", expand=True)

        foot = tk.Frame(self.page_main, bg=p["bg"])
        foot.pack(side="bottom", fill="x", padx=20, pady=(0, 14))
        ttk.Style(self).configure("Act.TButton", padding=(16, 6),
                                  font=("Microsoft YaHei UI", 10))
        self.btn_outdir = ttk.Button(foot, text="图库", style="Act.TButton",
                                     command=self.on_open_gallery)
        self.btn_outdir.pack(side="left")
        self.btn_repair = ttk.Button(foot, text="维修", style="Act.TButton",
                                     command=self.on_repair)
        self.btn_repair.pack(side="left", padx=8)
        self.btn_pack = ttk.Button(foot, text="打包", style="Act.TButton",
                                   command=self.on_pack)
        self.btn_pack.pack(side="left")
        self.btn_download = ttk.Button(foot, text="下载", style="Act.TButton",
                                       command=self.on_open_download)
        self.btn_download.pack(side="left", padx=8)
        self.btn_setup = ttk.Button(foot, text="选界面", style="Act.TButton",
                                    command=self.show_setup)
        self.btn_setup.pack(side="left")
        tk.Label(foot, text="把打包好的 zip 拖进窗口 = 自动安装", bg=p["bg"], fg=p["sub"],
                 font=font_small).pack(side="right")

        body = ttk.Frame(self.page_main, padding=(20, 8, 20, 16))
        body.pack(fill="both", expand=True)

        # ---- 左：内核登记册
        left = ttk.Frame(body, width=214)
        left.pack(side="left", fill="y", padx=(0, 16))
        left.pack_propagate(False)
        tk.Label(left, text="内核", bg=p["bg"], fg=p["sub"],
                 font=font_small).pack(anchor="w", pady=(0, 5))
        self.lb = tk.Listbox(
            left, bg=p["sunken"], fg=p["fg"], selectbackground=ACCENT,
            selectforeground=INK, bd=0, highlightthickness=1,
            highlightbackground=p["sunken"], highlightcolor=ACCENT,
            font=("Microsoft YaHei UI", 11), activestyle="none",
            exportselection=False, height=5)
        self.lb.pack(fill="x")
        self.lb.bind("<<ListboxSelect>>", lambda _e: self.refresh_detail())
        self.lbl_kernel = tk.Label(left, text="", bg=p["bg"], fg=p["sub"],
                                   font=font_mono, justify="left", anchor="w")
        self.lbl_kernel.pack(anchor="w", pady=(8, 0), fill="x")
        tk.Frame(left, bg=p["bg"]).pack(fill="both", expand=True)

        # ---- 右：侦探 + 动作 + 进度 + 日志
        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True)

        tk.Label(right, text="环境侦探", bg=p["bg"], fg=p["fg"],
                 font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", pady=(0, 6))
        card = tk.Frame(right, bg=p["sunken"], highlightthickness=1,
                        highlightbackground="#252c38")
        card.pack(fill="x")
        self.rows = {}
        for key in ("python", "models", "port", "disk", "gpu", "kernels"):
            row = tk.Frame(card, bg=p["sunken"])
            row.pack(fill="x", padx=12, pady=2)
            lamp = tk.Label(row, text="·", bg=p["sunken"], fg=p["sub"],
                            font=font_bold, width=2, anchor="w")
            lamp.pack(side="left")
            name = tk.Label(row, text="", bg=p["sunken"], fg=p["fg"],
                            font=font_bold, width=11, anchor="w")
            name.pack(side="left")
            val = tk.Label(row, text="检测中…", bg=p["sunken"], fg=p["sub"],
                           font=font_mono, anchor="w", justify="left", wraplength=520)
            val.pack(side="left", fill="x", expand=True)
            self.rows[key] = (lamp, name, val)
        card.bind("<Configure>", lambda e: self._relayout_card(e.width))

        # ---- 出图目录（核在 kernel.json 里自己声明，壳只显示不猜）
        outrow = tk.Frame(right, bg=p["bg"])
        outrow.pack(fill="x", pady=(10, 0))
        tk.Label(outrow, text="出图", bg=p["bg"], fg=p["sub"],
                 font=font_small).pack(side="left")
        self.lbl_out = tk.Label(outrow, text="—", bg=p["bg"], fg=p["fg"],
                                font=font_mono, anchor="w")
        self.lbl_out.pack(side="left", padx=(10, 0), fill="x", expand=True)

        # ---- 动作
        acts = tk.Frame(right, bg=p["bg"])
        acts.pack(fill="x", pady=(14, 0))
        self.btn_start = self._accent_button(acts, "▶  启动", self.on_start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(acts, text="■  停止", style="Act.TButton",
                                   command=self.on_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=8)
        self.btn_open = ttk.Button(acts, text="打开页面", style="Act.TButton",
                                   command=self.on_open, state="disabled")
        self.btn_open.pack(side="left")

        # ---- 进度（没进度时整行收起）
        self.progrow = tk.Frame(right, bg=p["bg"])
        self.bar = Gauge(self.progrow, p["bg"], height=10)
        self.bar.pack(side="left", fill="x", expand=True, pady=(6, 0))
        self.lbl_pct = tk.Label(self.progrow, text="", bg=p["bg"], fg=p["sub"],
                                font=font_small, width=6, anchor="e")
        self.lbl_pct.pack(side="left", padx=(8, 0), pady=(6, 0))

        # ---- 日志（吃掉剩余空间，别让它空占半窗）
        loghead = tk.Frame(right, bg=p["bg"])
        loghead.pack(fill="x", pady=(16, 4))
        self.loghead = loghead
        tk.Label(loghead, text="日志", bg=p["bg"], fg=p["sub"], font=font_small).pack(side="left")
        self.lbl_hint = tk.Label(loghead, text="", bg=p["bg"], fg=ACCENT, font=font_small)
        self.lbl_hint.pack(side="right")

        logf = tk.Frame(right, bg=p["bg"])
        logf.pack(fill="both", expand=True)
        self.log = tk.Text(logf, height=8, bg=p["sunken"], fg="#c9d1e0",
                           insertbackground="#c9d1e0", bd=0, relief="flat",
                           highlightthickness=1, highlightbackground="#252c38",
                           font=("Consolas", 9), wrap="word", padx=8, pady=6)
        sb = ttk.Scrollbar(logf, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)

        # ---- 图库页（默认不显示，点「图库」才 pack 上来）
        self.page_gallery = ttk.Frame(self)
        self._build_gallery()

        # ---- 首启引导页 / 下载页（同样是兄弟页，底栏或首启逻辑切过去）
        self.page_setup = ttk.Frame(self)
        self._build_setup()
        self.page_download = ttk.Frame(self)
        self._build_download()
        self.current_page = self.page_main

    # ------------------------------------------------------------------ 图库页
    def _build_gallery(self):
        p = self.P
        font_small = ("Microsoft YaHei UI", 9)
        g = self.page_gallery

        bar = tk.Frame(g, bg=p["bg"])
        bar.pack(fill="x", padx=20, pady=(10, 6))
        bar.grid_columnconfigure(2, weight=1)      # 只有状态列伸缩，按钮永远留在右边
        ttk.Button(bar, text="← 返回", style="Act.TButton",
                   command=self.on_close_gallery).grid(row=0, column=0, sticky="w")
        tk.Label(bar, text="图库", bg=p["bg"], fg=p["fg"],
                 font=("Microsoft YaHei UI", 14, "bold")).grid(row=0, column=1, padx=(12, 0), sticky="w")
        self.lbl_gal_stat = tk.Label(bar, text="", bg=p["bg"], fg=p["sub"],
                                     font=font_small, anchor="w")
        self.lbl_gal_stat.grid(row=0, column=2, padx=(12, 10), sticky="ew")
        ttk.Button(bar, text="打开文件夹", style="Act.TButton",
                   command=self.on_open_outputs).grid(row=0, column=4, sticky="e")
        ttk.Button(bar, text="刷新", style="Act.TButton",
                   command=self.load_gallery).grid(row=0, column=3, padx=8, sticky="e")

        wrap = tk.Frame(g, bg=p["bg"])
        wrap.pack(fill="both", expand=True, padx=20, pady=(0, 16))
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)
        self.gal_canvas = tk.Canvas(wrap, bg=p["bg"], highlightthickness=0, bd=0)
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self.gal_canvas.yview)
        self.gal_canvas.configure(yscrollcommand=vsb.set)
        vsb.grid(row=0, column=1, sticky="ns")     # grid 才不会被 canvas 挤成 0 宽
        self.gal_canvas.grid(row=0, column=0, sticky="nsew")

        self.gal_inner = tk.Frame(self.gal_canvas, bg=p["bg"])
        self._gal_win = self.gal_canvas.create_window((0, 0), window=self.gal_inner, anchor="nw")
        self.gal_inner.bind(
            "<Configure>",
            lambda _e: self.gal_canvas.configure(scrollregion=self.gal_canvas.bbox("all")))
        self.gal_canvas.bind("<Configure>", self._gal_on_resize)
        self._gal_token = 0
        self._gal_cols = 0
        self._gal_resize_job = None
        # 滚轮：鼠标压在任意格子上也要能滚，绑在 toplevel 上，靠标记判断是不是图库页
        self.bind_all("<MouseWheel>", self._gal_wheel)

    def _gal_cols_now(self):
        """按画布实际宽度算放几列 —— 窗口小就少放，别让最后一列顶到边上。"""
        w = self.gal_canvas.winfo_width()
        if w <= 1:
            return GALLERY_COLS
        return max(2, int((w - 16) // (THUMB_PX + 14)))

    def _gal_on_resize(self, event):
        self.gal_canvas.itemconfigure(self._gal_win, width=event.width)
        if not self.gallery_visible or not self._gal_cells:
            return
        if self._gal_cols_now() == self._gal_cols:
            return
        if self._gal_resize_job:
            try:
                self.after_cancel(self._gal_resize_job)
            except Exception:
                pass
        self._gal_resize_job = self.after(300, self.load_gallery)   # 缩略图有缓存，重排很快

    def _gal_wheel(self, event):
        if not self.gallery_visible:
            return
        self.gal_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def on_open_gallery(self):
        """切到图库页：主界面原样留着，内核跑着也照看。"""
        if self.gallery_visible:
            self.load_gallery()
            return
        self._show_page(self.page_gallery, "图库")
        self.load_gallery()

    def on_close_gallery(self):
        self._show_page(self.page_main)

    def _show_page(self, page, suffix=""):
        """兄弟页切换的唯一入口：谁上谁下、标题、图库滚轮开关都在这儿统一。"""
        for p in (self.page_main, self.page_gallery, self.page_setup, self.page_download):
            if p is not page:
                p.pack_forget()
        page.pack(fill="both", expand=True)
        self.gallery_visible = page is self.page_gallery
        self.current_page = page
        self.title(f"{APP_NAME} {VERSION}" + (f" · {suffix}" if suffix else ""))

    def _gal_clear(self):
        for w in self.gal_inner.winfo_children():
            w.destroy()
        self._gal_cells = []
        self._gal_photos = []

    def _gal_notice(self, text):
        self._gal_clear()
        tk.Label(self.gal_inner, text=text, bg=self.P["bg"], fg=self.P["sub"],
                 font=("Microsoft YaHei UI", 10), justify="left",
                 wraplength=640).pack(anchor="w", pady=(24, 0))

    def load_gallery(self):
        """扫当前内核声明的出图目录 → 缩略图落盘 → 主线程按日期分组铺格子。"""
        m = self.current_manifest()
        path = (m or {}).get("outputs")
        if not path:
            self.lbl_gal_stat.configure(text="")
            self._gal_notice("这个内核没在 kernel.json 里声明出图目录（outputs），图库没得看。")
            return
        if not os.path.isdir(path):
            self.lbl_gal_stat.configure(text="")
            self._gal_notice(f"出图目录还不存在：\n{path}\n\n出第一张图之后，这里就有东西了。")
            return
        self._gal_clear()
        self.lbl_gal_stat.configure(text=f"{self._short(path, 46)}  ·  扫描中…")
        self._gal_token += 1
        token = self._gal_token

        def work():
            try:
                items, total = scan_gallery(path)
                make_thumbs(items)
            except Exception as e:              # 目录被删/没权限，别把窗口带崩
                if token == self._gal_token:
                    self.after(0, lambda: self._gal_notice(f"扫描出图目录失败：{e}"))
                return
            try:
                self.after(0, lambda: self._gal_render(path, items, total, token))
            except Exception:                   # 窗口已销毁（测试里秒关）
                pass
        threading.Thread(target=work, daemon=True).start()

    def _gal_render(self, outputs_dir, items, total, token=None):
        if token is not None and token != self._gal_token:
            return                              # 期间又点了一次刷新，这批作废
        if not self.gallery_visible or not self.winfo_exists():
            return
        if not items:
            self.lbl_gal_stat.configure(text=f"0 张 · {self._short(outputs_dir, 30)}")
            self._gal_notice("出图目录里还没有图片。开跑一张，这里就有了。")
            return
        more = f" · 最近 {len(items)} 张" if total > len(items) else ""
        self.lbl_gal_stat.configure(text=f"共 {total} 张{more} · {self._short(outputs_dir, 30)}")
        self._gal_clear()

        p = self.P
        font_small = ("Microsoft YaHei UI", 9)
        font_tiny = ("Microsoft YaHei UI", 8)

        # 按日期分组（items 已按 mtime 倒序，顺序切段即可）
        cols = self._gal_cols_now()
        self._gal_cols = cols
        groups = []
        for it in items:
            day = datetime.date.fromtimestamp(it["mtime"])
            if groups and groups[-1][0] == day:
                groups[-1][1].append(it)
            else:
                groups.append((day, [it]))

        for gi, (day, arr) in enumerate(groups):
            head = tk.Frame(self.gal_inner, bg=p["bg"])
            head.pack(fill="x", pady=(14 if gi else 4, 6))
            tk.Label(head, text=_day_label(day), bg=p["bg"], fg=p["fg"],
                     font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
            tk.Label(head, text=f"  {len(arr)} 张", bg=p["bg"], fg=p["sub"],
                     font=font_small).pack(side="left")
            tk.Frame(head, bg="#252c38", height=1).pack(
                side="left", fill="x", expand=True, padx=(10, 0), pady=(6, 0))

            grid = tk.Frame(self.gal_inner, bg=p["bg"])
            grid.pack(anchor="w")
            r = c = 0
            for it in arr:
                cell = tk.Frame(grid, bg=p["bg"])
                cell.grid(row=r, column=c, padx=5, pady=5)
                box = tk.Frame(cell, width=THUMB_PX, height=THUMB_PX, bg=p["sunken"],
                               highlightthickness=1, highlightbackground="#252c38")
                box.pack()
                box.pack_propagate(False)
                thumb = tk.Label(box, text="…", bg=p["sunken"], fg=p["sub"],
                                 font=font_small, bd=0)
                thumb.place(relx=0.5, rely=0.5, anchor="center")
                name = os.path.basename(it["path"])
                if len(name) > 18:
                    name = name[:10] + "…" + name[-7:]
                hhmm = datetime.datetime.fromtimestamp(it["mtime"]).strftime("%H:%M")
                # 标题放进固定宽框：文件名再长也撑不宽格子（Tk 的 wraplength 不掐无空格长串）
                capwrap = tk.Frame(cell, width=THUMB_PX + 2, height=18, bg=p["bg"])
                capwrap.pack(fill="x", pady=(3, 0))
                capwrap.pack_propagate(False)
                cap = tk.Label(capwrap, text=f"{hhmm}  {name}", bg=p["bg"], fg=p["sub"],
                               font=font_tiny, anchor="w")
                cap.pack(fill="both", expand=True)
                for w in (box, thumb, cap):
                    w.bind("<Button-1>", lambda _e, path=it["path"]: self._gal_open(path))
                self._gal_cells.append((thumb, it))
                c += 1
                if c >= cols:
                    c, r = 0, r + 1

        self.gal_canvas.yview_moveto(0)
        self._gal_pump(0)

    def _gal_pump(self, start, batch=8):
        """分片加载缩略图：一次 8 张，窗口不假死。"""
        if not self.gallery_visible or not self.winfo_exists():
            return
        cells = self._gal_cells
        end = min(start + batch, len(cells))
        for lbl, it in cells[start:end]:
            self._gal_one(lbl, it)
        if end < len(cells):
            self.after(1, lambda: self._gal_pump(end))

    def _gal_one(self, lbl, it):
        if not lbl.winfo_exists():
            return
        try:
            if it.get("thumb"):
                ph = tk.PhotoImage(file=it["thumb"])
            elif it["path"].lower().endswith((".png", ".gif", ".bmp")) and _pil() is None:
                # 没 PIL 的便携包：Tk 8.6 能直接读 PNG，用整数倍取样缩
                ph = tk.PhotoImage(file=it["path"])
                k = max(1, -(-max(ph.width(), ph.height()) // THUMB_PX))
                if k > 1:
                    ph = ph.subsample(k, k)
            else:
                raise ValueError("no thumbnail")
        except Exception:
            lbl.configure(image="", text="无法预览", fg=self.P["sub"])
            return
        self._gal_photos.append(ph)          # 引用留住，否则被 GC 掉变空白
        lbl.configure(image=ph, text="")

    def _gal_open(self, path):
        try:
            os.startfile(path)
        except AttributeError:
            winproc.popen(["explorer", path])
        except OSError as e:
            messagebox.showerror(APP_NAME, f"打不开图片：{e}\n{path}")

    def _relayout_card(self, width):
        """卡片变宽/变窄时重设换行宽度，长值（显卡那句）不再被右边缘切掉。"""
        wrap = max(220, width - 190)
        for _lamp, _name, val in self.rows.values():
            val.configure(wraplength=wrap)

    def _accent_button(self, parent, text, command):
        """橙色主按钮：sv-ttk 改不了强调色，这只自己画。"""
        btn = tk.Button(parent, text=text, command=command, bg=ACCENT, fg=INK,
                        activebackground=ACCENT_HOVER, activeforeground=INK,
                        bd=0, relief="flat", cursor="hand2",
                        font=("Microsoft YaHei UI", 11, "bold"),
                        padx=20, pady=6, highlightthickness=0)
        btn.bind("<Enter>", lambda _e: btn.configure(bg=ACCENT_HOVER)
                 if str(btn["state"]) != "disabled" else None)
        btn.bind("<Leave>", lambda _e: btn.configure(bg=ACCENT)
                 if str(btn["state"]) != "disabled" else None)
        return btn

    # ------------------------------------------------------------------ 日志
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
        self.lb.delete(0, "end")
        self.ids = list(self.manifests)
        for kid in self.ids:
            m = self.manifests[kid]
            self.lb.insert("end", f"  {kid}    v{m.get('version', '?')}")
        if not self.ids:
            self.logline("✗ kernels/ 下没有可用内核，先放一个 kernel.json")
            self.lbl_kernel.configure(text="无内核")
            self.refresh_detect()
            return
        # 默认选主力内核：neoforge-sd15 是日常用的那条，别按名字排序把别的顶上去
        want = "neoforge-sd15" if "neoforge-sd15" in self.ids else (self.ids[0] if self.ids else None)
        if want:
            self.lb.selection_set(self.ids.index(want))
        self.logline(f"内核登记册：{len(self.ids)} 个（{', '.join(self.ids)}）")
        self.refresh_detail()
        self.refresh_detect()

    def current_manifest(self):
        sel = self.lb.curselection()
        if not sel or sel[0] >= len(self.ids):
            return None
        return self.manifests.get(self.ids[sel[0]])

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
            lamp, name, val = pair
            name.configure(text=it["label"])
            level = it["level"]
            lamp.configure(text=LEVEL_ICON.get(level, "·"),
                           fg=LEVEL_COLOR.get(level, self.P["sub"]))
            val.configure(text=it["detail"],
                          fg=self.P["fg"] if level == detect.GREEN else LEVEL_COLOR.get(level, self.P["fg"]))
        for it in items:
            if it["level"] != detect.GREEN and it["fix"]:
                self.logline(f"{LEVEL_ICON[it['level']]} {it['label']}：{it['fix']}")
        level, summary = detect.verdict(items)
        if not self.busy and time.time() > getattr(self, "_state_pin", 0):
            mark = "" if level == detect.GREEN else "⚠ "
            self.lbl_state.configure(text=f"{mark}{summary}",
                                     fg=LEVEL_COLOR.get(level, self.P["sub"]))

    def refresh_detail(self):
        m = self.current_manifest()
        if not m:
            return
        self.lbl_kernel.configure(
            text=f"{display_name(m)}\n端口 {m['port']} · {m.get('dialect', '-')}\n"
                 f"{os.path.basename(m['command'][0])}")
        self.refresh_outputs()
        self.refresh_port()
        if getattr(self, "gallery_visible", False):
            self.load_gallery()          # 图库正开着换内核：跟着换目录
        self.logline(f"选中内核 {m['id']}：{m['command'][0]} … 端口 {m['port']} 健康 {m['health']['path']}")

    # ---------------------------------------------------------------- 出图
    def refresh_outputs(self):
        """出图目录由 kernel.json 的 outputs 声明，壳只显示 + 数张数，不猜后端布局。"""
        m = self.current_manifest()
        if not m:
            self.lbl_out.configure(text="—", fg=self.P["sub"])
            return
        path = m.get("outputs")
        if not path:
            self.lbl_out.configure(text="该内核未声明出图目录", fg=self.P["sub"])
            return
        self.lbl_out.configure(text=f"{self._short(path)}  ·  统计中…", fg=self.P["sub"])

        def work():
            n, newest = selfcheck.scan_outputs(path)
            try:
                self.after(0, lambda: self._apply_outputs(path, n, newest))
            except Exception:           # 窗口已销毁（测试里秒关）
                pass
        threading.Thread(target=work, daemon=True).start()

    def _apply_outputs(self, path, n, newest):
        if n is None:
            self.lbl_out.configure(text=f"{self._short(path)}  ·  目录还没建，首次出图会创建",
                                   fg=self.P["sub"])
            return
        tail = f"{n} 张"
        if newest:
            tail += f" · 最近 {os.path.basename(newest)}"
        self.lbl_out.configure(text=f"{self._short(path)}  ·  {tail}", fg=self.P["fg"])

    @staticmethod
    def _short(path, limit=44):
        if len(path) <= limit:
            return path
        half = limit // 2 - 1
        return f"{path[:half]}…{path[-half:]}"

    def on_open_outputs(self):
        m = self.current_manifest()
        path = (m or {}).get("outputs")
        if not path:
            return
        os.makedirs(path, exist_ok=True)
        try:
            os.startfile(path)                      # Windows 资源管理器
        except AttributeError:
            import subprocess
            winproc.popen(["explorer", path])
        except OSError as e:
            messagebox.showerror(APP_NAME, f"打不开目录：{e}\n{path}")

    # ---------------------------------------------------------------- 维修
    def on_repair(self):
        """诊断 → 自动修能修的 → 复检。启动器自己玩崩了也能用（命令行 python -m core.repair 同一条路）。"""
        if self.busy or getattr(self, "_repairing", False):
            return
        self._repairing = True
        self.btn_repair.configure(state="disabled")
        self.logline("—— 维修：诊断 → 自动修 → 复检 ——")
        port = (self.current_manifest() or {}).get("port")
        own_ports = (port,) if self._running() and port else ()

        def show_act(a):
            icon = "✓" if a["ok"] else "✗"
            self.after(0, lambda i=icon, k=a["kind"], d=a["detail"]:
                       self.logline(f"{i} 维修 · {k}：{d}"))

        def work():
            try:
                actions, items, level, summary = repair.run(
                    BASE_DIR, progress=show_act, own_ports=own_ports)
                path = repair.write_report(actions, items, level, summary, BASE_DIR)
            except Exception as e:
                self.after(0, lambda: self.logline(f"✗ 维修自身出错：{e}"))
                self.after(0, lambda: self._repair_done(None, None, None, None, None))
                return
            self.after(0, lambda: self._repair_done(actions, items, level, summary, path))

        threading.Thread(target=work, daemon=True).start()

    def _repair_done(self, actions, items, level, summary, path):
        self._repairing = False
        self.btn_repair.configure(state="normal")
        if items is None:
            return
        for it in items:
            if it["level"] != detect.GREEN:
                self.logline(f"{LEVEL_ICON[it['level']]} 复检 · {it['label']}：{it['detail']}")
        self.refresh_detect()          # 先让侦探把卡片刷成最新，别让它盖掉维修结论
        self.refresh_outputs()
        mark = "" if level == detect.GREEN else "⚠ "
        self._state_pin = time.time() + 8      # 侦探 0.2s 后回来，别把结论盖掉
        self.lbl_state.configure(text=f"{mark}维修后：{summary}",
                                 fg=LEVEL_COLOR.get(level, self.P["sub"]))
        self.logline(f"维修结论：{summary}")
        self.logline(f"报告：{path}")
        if os.environ.get("NEOFORGE_SILENT") == "1":     # 脚本/自测：只记日志，不弹窗
            return
        if messagebox.askyesno(f"{APP_NAME} · 维修",
                               f"{summary}\n\n报告已写入：\n{path}\n\n现在打开吗？"):
            try:
                os.startfile(path)
            except (AttributeError, OSError):
                pass

    # ------------------------------------------------------------ 首启引导（选 UI）
    def maybe_first_run(self):
        """第一次用才问：没做过选择、也没有后端 python → 直接落在引导页。

        拖整合包装成功的一律写 setup.json，所以「拉包进来」这条路不会触发这一页。
        """
        if read_setup() is not None:
            return
        if has_backend():
            return
        self.logline(f"第一次使用（{INSTALLER_LABEL}）：还没装后端，先选一条出图路线")
        self.show_setup()

    def show_setup(self):
        # 引导页两张卡片内容多，窗口太小会把按钮挤没：先把它撑到够高够宽
        try:
            self.update_idletasks()
            w, h = self.winfo_width(), self.winfo_height()
            if w <= 1 or h <= 1:              # 还没映射到屏幕：读 geometry 里要的那个尺寸
                cur = self.geometry().split("+")[0]
                w, h = (int(x) for x in cur.lower().split("x"))
            if w < 980 or h < 720:
                self.geometry(f"{max(w, 980)}x{max(h, 720)}")
        except (tk.TclError, ValueError):
            pass
        self._show_page(self.page_setup, "第一次用 · 选界面")
        self.logline("引导页：A1111 与 ComfyUI 的优劣都写在卡片上，选一个就行")

    def _build_setup(self):
        p = self.P
        g = self.page_setup
        head = tk.Frame(g, bg=p["bg"])
        head.pack(fill="x", padx=24, pady=(16, 2))
        tk.Label(head, text="第一次用：先选一条出图路线", bg=p["bg"], fg=p["fg"],
                 font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w")
        tk.Label(head, text="这一步只决定「用哪个界面后端」。两个都能出图，区别在生态、速度和显存。"
                            "选完会跳到下载页，缺的模型/插件在那儿点着下。",
                 bg=p["bg"], fg=p["sub"], font=("Microsoft YaHei UI", 9),
                 justify="left", wraplength=900).pack(anchor="w", pady=(4, 0))

        # 先占住底部：卡片再高，这排按钮也不会被挤出去
        foot = tk.Frame(g, bg=p["bg"])
        foot.pack(side="bottom", fill="x", padx=24, pady=(4, 14))
        ttk.Button(foot, text="我有整合包，等会儿自己拖进来", style="Act.TButton",
                   command=self.on_skip_setup).pack(side="left")
        tk.Label(foot, text="把整合包拖进窗口就不会再问这一步 —— 装完自动记下",
                 bg=p["bg"], fg=p["sub"], font=("Microsoft YaHei UI", 9)).pack(side="right")

        cards = tk.Frame(g, bg=p["bg"])
        cards.pack(fill="both", expand=True, padx=24, pady=(8, 4))
        cards.grid_columnconfigure(0, weight=1)
        for row, key in enumerate(("forge", "comfyui")):
            self._build_choice_card(cards, row, key)

    def _build_choice_card(self, parent, row, key):
        """一张卡横着排：左边 标题/一句话/按钮，右边 优点 | 缺点 两列。

        竖着堆两张卡、而不是并排 —— 并排在窄窗口上会被窗口边缘切掉，
        横排的卡片永远跟着页宽走，缺点列表也不会被挤到框外。
        """
        p = self.P
        info = cat.UI_CHOICE_TEXT[key]
        recommend = key == "forge"
        card = tk.Frame(parent, bg=p["sunken"], highlightthickness=1,
                        highlightbackground=ACCENT if recommend else "#252c38")
        card.grid(row=row, column=0, sticky="ew", pady=(0, 10))

        inner = tk.Frame(card, bg=p["sunken"])
        inner.pack(fill="x", padx=14, pady=12)

        left = tk.Frame(inner, bg=p["sunken"], width=200)
        left.pack(side="left", fill="y", padx=(0, 16))
        left.pack_propagate(False)
        title = tk.Frame(left, bg=p["sunken"])
        title.pack(fill="x")
        tk.Label(title, text=info["title"], bg=p["sunken"], fg=p["fg"],
                 font=("Microsoft YaHei UI", 13, "bold")).pack(side="left")
        if recommend:
            tk.Label(title, text=" 推荐", bg=p["sunken"], fg=ACCENT,
                     font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")
        tk.Label(left, text=hard_wrap(info["tagline"], 26), bg=p["sunken"],
                 fg=ACCENT if recommend else p["sub"], font=("Microsoft YaHei UI", 9),
                 justify="left", anchor="w", wraplength=0).pack(anchor="w", pady=(2, 4))
        tk.Label(left, text=hard_wrap(info["pick"], 26), bg=p["sunken"], fg=p["sub"],
                 font=("Microsoft YaHei UI", 9), justify="left", anchor="w",
                 wraplength=0).pack(anchor="w", pady=(0, 8))
        if recommend:
            btn = self._accent_button(left, "就用 WebUI(Forge)", lambda: self.on_choose_ui("forge"))
        else:
            btn = ttk.Button(left, text="就用 ComfyUI", style="Act.TButton",
                             command=lambda: self.on_choose_ui("comfyui"))
        btn.pack(anchor="w")

        right = tk.Frame(inner, bg=p["sunken"])
        right.pack(side="left", fill="both", expand=True)
        bullets = []                # 右两列正文标签：按实际宽度动态换行，绝不越出卡片
        for i, (label, lines, color, mark) in enumerate(
                (("优点", info["pros"], "#3ddc84", "＋"), ("缺点", info["cons"], "#ff9457", "－"))):
            col = tk.Frame(right, bg=p["sunken"])
            col.pack(side="left", fill="both", expand=True, padx=(0, 10) if i == 0 else (10, 0))
            tk.Label(col, text=label, bg=p["sunken"], fg=color,
                     font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(0, 3))
            for line in lines:
                rowf = tk.Frame(col, bg=p["sunken"])
                rowf.pack(fill="x", anchor="w", pady=1)
                tk.Label(rowf, text=mark, bg=p["sunken"], fg=color, width=2, anchor="w",
                         font=("Microsoft YaHei UI", 9)).pack(side="left")
                lbl = tk.Label(rowf, text=hard_wrap(line, 46), bg=p["sunken"], fg=p["fg"],
                               font=("Microsoft YaHei UI", 9), justify="left", anchor="w",
                               width=26, wraplength=0)  # 换行已由 hard_wrap 排好，width 只用来掐住请求宽度
                lbl.pack(side="left", fill="x", expand=True)
                bullets.append(lbl)


    def on_choose_ui(self, key):
        info = cat.UI_CHOICE_TEXT[key]
        write_setup(key)
        self.logline(f"已选定出图界面：{info['title']}（写进 {os.path.basename(SETUP_FILE)}，下次不再问）")
        self.on_open_download(preselect=key)

    def on_skip_setup(self):
        write_setup("skip", note="用户选择先跳过（准备拖整合包）")
        self.logline("已跳过引导：拖整合包进来照样能装，装完这条会被覆盖成 package")
        self._show_page(self.page_main)

    # ---------------------------------------------------------------- 下载页
    def _build_download(self):
        p = self.P
        g = self.page_download
        bar = tk.Frame(g, bg=p["bg"])
        bar.pack(fill="x", padx=20, pady=(10, 4))
        ttk.Button(bar, text="← 返回", style="Act.TButton",
                   command=self.on_close_download).pack(side="left")
        tk.Label(bar, text="下载", bg=p["bg"], fg=p["fg"],
                 font=("Microsoft YaHei UI", 14, "bold")).pack(side="left", padx=(12, 0))
        self.lbl_dl_stat = tk.Label(bar, text="", bg=p["bg"], fg=p["sub"],
                                    font=("Microsoft YaHei UI", 9), anchor="e")
        self.lbl_dl_stat.pack(side="right")

        frow = tk.Frame(g, bg=p["bg"])
        frow.pack(fill="x", padx=20, pady=(2, 6))
        self.dl_kind = tk.StringVar(value="all")
        for label, val in (("全部", "all"),) + tuple((cat.KINDS[k], k) for k in cat.KIND_ORDER):
            ttk.Radiobutton(frow, text=label, value=val, variable=self.dl_kind,
                            command=self.refresh_downloads).pack(side="left", padx=(0, 12))

        # 搜索 + 在线清单（PCL 下载页顶上那条的等价物：找得到、看得见类型）
        srow = tk.Frame(g, bg=p["bg"])
        srow.pack(fill="x", padx=20, pady=(0, 6))
        tk.Label(srow, text="搜索", bg=p["bg"], fg=p["fg"],
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(0, 6))
        self.dl_query = tk.StringVar(value="")
        ttk.Entry(srow, textvariable=self.dl_query, width=26).pack(side="left", padx=(0, 10))
        self.dl_query.trace_add("write", lambda *_a: self.refresh_downloads())
        ttk.Button(srow, text="粘贴链接添加", style="Act.TButton",
                   command=self.on_add_by_link).pack(side="left")
        self.btn_dl_online = ttk.Button(srow, text="搜魔搭", style="Act.TButton",
                                        command=self.on_fetch_online)
        self.btn_dl_online.pack(side="left", padx=8)

        wrap = tk.Frame(g, bg=p["bg"])
        wrap.pack(fill="both", expand=True, padx=20, pady=(0, 6))
        cols = ("type", "name", "size", "hot", "src", "state")
        self.dl_tree = ttk.Treeview(wrap, columns=cols, show="headings",
                                    selectmode="extended", height=12)
        for col, text, width, anchor in (("type", "类型", 104, "w"), ("name", "名称", 320, "w"),
                                         ("size", "大小", 86, "e"), ("hot", "热度", 82, "e"),
                                         ("src", "来源", 88, "w"), ("state", "状态", 88, "w")):
            self.dl_tree.heading(col, text=text)
            self.dl_tree.column(col, width=width, anchor=anchor)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.dl_tree.yview)
        self.dl_tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.dl_tree.pack(side="left", fill="both", expand=True)
        self.dl_tree.bind("<Double-1>", lambda _e: self.on_download_selected())
        self.dl_tree.bind("<<TreeviewSelect>>", lambda _e: self._dl_show_desc())

        self.dl_prog = Gauge(g, p["bg"], height=10)
        self.dl_prog.pack(fill="x", padx=20, pady=(2, 2))
        self.lbl_dl_hint = tk.Label(g, text="", bg=p["bg"], fg=p["sub"],
                                    font=("Microsoft YaHei UI", 9), anchor="w")
        self.lbl_dl_hint.pack(fill="x", padx=20)
        self.lbl_dl_desc = tk.Label(g, text="", bg=p["bg"], fg=p["sub"],
                                    font=("Microsoft YaHei UI", 9), justify="left",
                                    anchor="w", wraplength=820)
        self.lbl_dl_desc.pack(fill="x", padx=20, pady=(4, 0))

        arow = tk.Frame(g, bg=p["bg"])
        arow.pack(fill="x", padx=20, pady=(6, 14))
        ttk.Button(arow, text="下载选中（双击一行也行）", style="Act.TButton",
                   command=self.on_download_selected).pack(side="left")
        self.btn_dl_stop = ttk.Button(arow, text="停止", style="Act.TButton",
                                      command=self.on_download_stop, state="disabled")
        self.btn_dl_stop.pack(side="left", padx=8)
        ttk.Button(arow, text="打开下载目录", style="Act.TButton",
                   command=self.on_open_downloads).pack(side="left")
        self._dl_items = []
        self._dl_cancel = None
        self._downloading = False

    def on_open_download(self, preselect=None):
        self._show_page(self.page_download, "下载")
        self.refresh_downloads()
        self._maybe_refresh_popularity()
        if preselect:
            want = {f"ui-{preselect}", "sd15-emaonly", "vae-ft-mse"}
            if preselect == "forge":
                want |= {"ext-adetailer", "gfpgan-v14"}
            picked = [i for i in want if i in self.dl_tree.get_children()]
            self.dl_tree.selection_set(picked)
            self.logline(f"下载页已预选 {len(picked)} 项（先把 {cat.UI_CHOICE_TEXT[preselect]['title']} 这一路配齐）")

    def _maybe_refresh_popularity(self):
        """打开下载页时，热度缓存超过 7 天就在后台刷一次（不挡界面）。"""
        if getattr(self, "_pop_busy", False) or not pop_util.stale(BASE_DIR):
            return
        self._pop_busy = True

        def work():
            try:
                pop_util.refresh(BASE_DIR, log=lambda m: self.after(0, lambda: self.logline(m)))
            except Exception as e:                # noqa: BLE001  拉不到就用内置值，不打扰
                self.after(0, lambda e=e: self.logline(f"热度榜没拉到（{e}），用内置实测值"))
            finally:
                self._pop_busy = False
                self.after(0, self.refresh_downloads)

        threading.Thread(target=work, daemon=True).start()

    def on_close_download(self):
        self._show_page(self.page_main)

    def refresh_downloads(self):
        if not self.winfo_exists():
            return
        kind = getattr(self, "dl_kind", None)
        want = kind.get() if kind else "all"
        qv = getattr(self, "dl_query", None)
        q = (qv.get() if qv else "").strip().lower()
        self._dl_items = cat.load(BASE_DIR)
        # 排行：内置优先（实测过、有推荐理由）→ 同类内按热度（GitHub stars）降序 → 名称
        def _key(it):
            k = it.get("kind") or ""
            order = cat.KIND_ORDER.index(k) if k in cat.KIND_ORDER else 99
            builtin = 0 if it.get("source") != "modelscope" else 1
            return (order, builtin, -(it.get("stars") or 0), it.get("name") or "")
        self._dl_items.sort(key=_key)
        keep = {it["id"]: (self.dl_tree.set(i, "state") if self.dl_tree.exists(i) else "")
                for it in self._dl_items for i in (it["id"],) if self.dl_tree.exists(i)}
        for iid in self.dl_tree.get_children():
            self.dl_tree.delete(iid)
        n = 0
        for it in self._dl_items:
            if want != "all" and it.get("kind") != want:
                continue
            sub = cat.subtype_of(it)
            if q:
                blob = " ".join((it.get("name", ""), sub, it.get("desc", ""),
                                 it.get("repo", ""), it.get("id", ""))).lower()
                if q not in blob:
                    continue
            size = f"{it['size']:.0f} MiB" if it.get("size") else "—"
            state = keep.get(it["id"]) or ("✓ 已在位" if downloader.installed(it, BASE_DIR) else "未下")
            src = "魔搭" if it.get("source") == "modelscope" else "内置"
            hot = ("★" + pop_util.human(it["stars"])) if it.get("stars") else "—"
            self.dl_tree.insert("", "end", iid=it["id"],
                                values=(sub, it["name"], size, hot, src, state))
            n += 1
        self.lbl_dl_stat.configure(text=f"{n} 项 · 落点 {BASE_DIR}")


    # ------------------------------------------------------------ 在线清单（魔搭）
    def on_add_by_link(self):
        """粘贴一个 ModelScope 模型链接（或 owner/name），解析成条目加进清单。"""
        text = simpledialog.askstring("添加模型", "粘贴魔搭模型链接或 owner/name：", parent=self)
        if not text:
            return
        repo = msremote.parse_ref(text)
        if not repo:
            messagebox.showwarning(
                "认不出", f"这不是模型地址：{text}\n要的是 https://modelscope.cn/models/<owner>/<name>",
                parent=self)
            return
        self.lbl_dl_hint.configure(text=f"正在问魔搭：{repo} …")

        def work():
            try:
                item = msremote.to_item(repo)
            except msremote.FetchError as e:
                self.after(0, lambda e=e: self.lbl_dl_hint.configure(text=f"✗ {repo}：{e}"))
                self.after(0, lambda e=e: self.logline(f"✗ 添加 {repo} 失败：{e}"))
                return
            if not item:
                msg = f"{repo} 里没有可下的权重文件（可能只有代码/配置）"
                self.after(0, lambda: self.lbl_dl_hint.configure(text="✗ " + msg))
                self.after(0, lambda: self.logline("✗ " + msg))
                return
            items = {i["id"]: i for i in msremote.load_cache(BASE_DIR)}
            items[item["id"]] = item
            msremote.save_cache(BASE_DIR, list(items.values()))
            self.after(0, lambda: self.lbl_dl_hint.configure(
                text=f"✓ 已加入：{item['subtype']} · {item['name']}（{item['size']:.0f} MiB）"))
            self.after(0, lambda: self.logline(
                f"在线清单 +1：{item['subtype']} · {item['name']}（{item['size']:.0f} MiB）"))
            self.after(0, self.refresh_downloads)

        threading.Thread(target=work, daemon=True).start()

    def on_fetch_online(self):
        """拿搜索框里的词问魔搭，把结果并进清单（等价于 PCL 的远程下载清单）。"""
        if getattr(self, "_online_busy", False):
            self.logline("上一批还在搜，等它跑完")
            return
        kw = (self.dl_query.get() if hasattr(self, "dl_query") else "").strip()
        if not kw:
            kw = "stable-diffusion"
            self.logline("没输关键词，按 stable-diffusion 搜（想搜别的就写在搜索框里）")
        self._online_busy = True
        self.btn_dl_online.configure(state="disabled")
        self.lbl_dl_hint.configure(text=f"正在问魔搭：{kw} …")

        def work():
            try:
                got = msremote.search(kw, page=1, page_size=30, sort="downloads")
                items = {i["id"]: i for i in msremote.load_cache(BASE_DIR)}
                added = 0
                for it in got:
                    old = items.get(it["id"])
                    # 没 hydrate 过的（还没下过）就用最新元数据覆盖：类型/体积修好了能跟着更新
                    if old is None:
                        items[it["id"]] = it
                        added += 1
                    elif not old.get("urls"):
                        items[it["id"]] = it
                msremote.save_cache(BASE_DIR, list(items.values()))
            except Exception as e:                    # noqa: BLE001  网络/解析都别崩窗口
                msg = f"✗ 搜魔搭失败：{e}"
                self.after(0, lambda m=msg: self.lbl_dl_hint.configure(text=m))
                self.after(0, lambda m=msg: self.logline(m))
                self.after(0, self._online_done)
                return
            self.after(0, lambda: self.lbl_dl_hint.configure(
                text=f"魔搭「{kw}」：返回 {len(got)} 条，新增 {added} 条"))
            self.after(0, lambda: self.logline(
                f"魔搭搜索「{kw}」：返回 {len(got)} 条，新入清单 {added} 条"))
            self.after(0, lambda: self.dl_query.set(""))     # 清掉关键词，别把新结果过滤没了
            self.after(0, self.refresh_downloads)
            self.after(0, self._online_done)

        threading.Thread(target=work, daemon=True).start()


    def _online_log(self, m):
        self.lbl_dl_hint.configure(text=m.strip())
        self.logline("魔搭 · " + m.strip())

    def _online_done(self):
        self._online_busy = False
        try:
            self.btn_dl_online.configure(state="normal")
        except tk.TclError:
            pass

    def _dl_show_desc(self):
        sel = self.dl_tree.selection()
        if not sel:
            self.lbl_dl_desc.configure(text="")
            return
        it = cat.find(self._dl_items, sel[0])
        if it:
            self.lbl_dl_desc.configure(text=f"{it['name']}：{it.get('desc', '')}")

    def _dl_state(self, item_id, text):
        if self.winfo_exists() and self.dl_tree.exists(item_id):
            self.dl_tree.set(item_id, "state", text)

    def _dl_tick(self, frac, text):
        self.dl_prog.set(frac)
        self.lbl_dl_hint.configure(text=text)

    def on_download_selected(self):
        if self._downloading:
            self.logline("已经有一批在下：先等它跑完，或点「停止」")
            return
        sel = [s for s in self.dl_tree.selection()]
        queue = [cat.find(self._dl_items, s) for s in sel]
        queue = [it for it in queue if it]
        if not queue:
            self.logline("没选条目：在列表里点几行（或双击一行）再下")
            return
        self._downloading = True
        self._dl_cancel = threading.Event()
        self.btn_dl_stop.configure(state="normal")
        self.dl_prog.set(0)
        self.logline(f"—— 开始下载 {len(queue)} 项 ——")

        def work():
            ok = fail = 0
            for it in queue:
                if self._dl_cancel.is_set():
                    break
                if it.get("repo") and not it.get("urls"):
                    self.after(0, lambda i=it["id"]: self._dl_state(i, "解析中…"))
                    it = msremote.hydrate(it)
                    self.after(0, lambda i=it["id"]: self._dl_state(i, "下载中…"))
                else:
                    self.after(0, lambda i=it["id"]: self._dl_state(i, "下载中…"))
                try:
                    res = downloader.fetch(
                        it, BASE_DIR,
                        progress=lambda f, t: self.after(0, lambda f=f, t=t: self._dl_tick(f, t)),
                        log=lambda m: self.after(0, lambda m=m: self.logline(m)),
                        cancel=self._dl_cancel)
                except downloader.Cancelled:
                    self.after(0, lambda i=it["id"]: self._dl_state(i, "已停止"))
                    break
                except Exception as e:                       # 网络/磁盘，别把窗口带崩
                    self.after(0, lambda e=e, i=it: self.logline(f"✗ {i['name']}：{e}"))
                    res = {"ok": False, "why": str(e)}
                if res.get("ok"):
                    ok += 1
                    self.after(0, lambda i=it["id"]: self._dl_state(i, "✓ 完成"))
                else:
                    fail += 1
                    self.after(0, lambda i=it["id"]: self._dl_state(i, "✗ 失败"))
                    if res.get("why"):
                        self.after(0, lambda w=res["why"], n2=it["name"]:
                                   self.logline(f"✗ {n2}：{w}"))
            self.after(0, lambda: self._dl_done(ok, fail))

        threading.Thread(target=work, daemon=True).start()

    def on_download_stop(self):
        if self._dl_cancel is not None:
            self._dl_cancel.set()
            self.logline("收到停止：当前文件会留在 .part，下次从断点接着下")
            self.btn_dl_stop.configure(state="disabled")

    def _dl_done(self, ok, fail):
        self._downloading = False
        self.btn_dl_stop.configure(state="disabled")
        self.lbl_dl_hint.configure(text=f"本轮：成功 {ok} · 失败 {fail}")
        self.logline(f"—— 下载结束：成功 {ok}，失败 {fail} ——")
        self.refresh_downloads()
        self.reload_kernels()          # 下完 ComfyUI 会多一个内核，登记册刷新一下
        if not SILENT and ok:
            messagebox.showinfo(f"{APP_NAME} · 下载完成",
                                f"成功 {ok} 项，失败 {fail} 项。\n\n落点：{BASE_DIR}\n"
                                f"细节看窗口日志。")

    def on_open_downloads(self):
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        try:
            os.startfile(DOWNLOAD_DIR)
        except (AttributeError, OSError):
            winproc.popen(["explorer", DOWNLOAD_DIR])

    # ---------------------------------------------------------------- 打包
    def _busy_bar(self, on):
        """进度条那一行的拉出/收回（打包和自动安装共用）。"""
        if on:
            self.bar.set(0)
            self.lbl_pct.configure(text="0%")
            self.progrow.pack(fill="x", pady=(12, 0), before=self.loghead)
        else:
            self.progrow.pack_forget()
            self.lbl_hint.configure(text="")
            self.lbl_pct.configure(text="")

    def _bar_tick(self, frac, text=""):
        self.bar.set(frac)
        self.lbl_pct.configure(text=f"{frac:.0%}")
        if text:
            self.lbl_hint.configure(text=os.path.basename(text) if os.sep in text else text)

    def on_pack(self):
        """把当前这份壳打成 zip —— 不含 backend/models（那 225 GB 走 _dist 的 7z 分卷老路）。"""
        if getattr(self, "_packing", False) or getattr(self, "_installing", False):
            return
        self._packing = True
        self.btn_pack.configure(state="disabled")
        out = os.path.join(PACK_DIR, f"{APP_NAME}-{VERSION}-{time.strftime('%Y%m%d-%H%M%S')}.zip")
        self.logline("—— 打包：壳 + 内核 → zip（不含 backend/models）——")
        self._busy_bar(True)

        def work():
            try:
                res = packer.build(
                    BASE_DIR, out_path=out, version=VERSION,
                    progress=lambda f, t: self.after(0, lambda f=f, t=t: self._bar_tick(f, t)),
                    log=lambda m: self.after(0, lambda m=m: self.logline(m)))
            except Exception as e:
                self.after(0, lambda e=e: self._pack_failed(e))
                return
            self.after(0, lambda r=res: self._pack_done(r))

        threading.Thread(target=work, daemon=True).start()

    def _pack_failed(self, exc):
        self._packing = False
        self.btn_pack.configure(state="normal")
        self._busy_bar(False)
        self.logline(f"✗ 打包失败：{exc}")
        if not SILENT:
            messagebox.showerror(f"{APP_NAME} · 打包失败", str(exc))

    def _pack_done(self, res):
        self._packing = False
        self.btn_pack.configure(state="normal")
        self._bar_tick(1.0, "打包完成")
        self.after(500, lambda: self._busy_bar(False))
        self.logline(f"包：{res['path']}")
        self.logline(f"sha256：{res['sha256']}")
        if SILENT:
            return
        if messagebox.askyesno(
                f"{APP_NAME} · 打包完成",
                f"{os.path.basename(res['path'])}\n\n"
                f"{res['files']} 个文件 · {res['bytes'] / 1048576:.2f} MiB · {res['seconds']:.1f}s\n"
                f"路径：{res['path']}\n\n现在打开包所在的文件夹吗？"):
            try:
                os.startfile(os.path.dirname(res["path"]))
            except (AttributeError, OSError):
                pass

    # ------------------------------------------------------------ 拖进来 = 自动安装
    def _enable_drop(self):
        self.drop = droptarget.enable(self, self.on_drop)
        if self.drop:
            self.logline("拖放就位：把打包好的 zip 拖进窗口，自动校验 + 安装")
        else:
            self.logline("⚠ 拖放没挂上（非 Windows 或被系统拒了）：还能点「打包」；"
                         "命令行装用 python -m core.installer 包.zip")

    def on_drop(self, paths):
        for p in paths or ():
            self.accept_package(p)

    def accept_package(self, path):
        """拖进来的东西：是包就装，不是就说清为什么（不含糊、不假装）。"""
        path = os.path.abspath(path)
        if getattr(self, "_installing", False):
            self.logline(f"忽略 {os.path.basename(path)}：正在装别的包")
            return
        if not os.path.isfile(path):
            self.logline(f"✗ 拖进来的不是文件：{path}")
            return
        insp = installer.inspect(path)
        if not insp["ok"]:
            # 不是我们的安装包 → 看看是不是目录里某个整合包（浏览器下完拖进来的那种）
            hit = downloader.match_archive(path, BASE_DIR)
            if hit:
                self.logline(f"—— 这是 {hit['name']} 的压缩包，直接解开 ——")
                self._installing = True
                self._busy_bar(True)

                def work_archive():
                    try:
                        res = downloader.adopt_archive(
                            path, BASE_DIR,
                            progress=lambda f, t: self.after(0, lambda f=f, t=t: self._dl_tick(f, t)),
                            log=lambda m: self.after(0, lambda m=m: self.logline(m)))
                    except Exception as e:
                        self.after(0, lambda e=e: self.logline(f"✗ 解包失败：{e}"))
                        res = {"ok": False, "why": str(e)}
                    if res is None:
                        res = {"ok": False, "why": "认不出这个压缩包"}
                    self.after(0, lambda r=res: self._install_done(r))

                threading.Thread(target=work_archive, daemon=True).start()
                return
            self.logline(f"✗ 不装 {os.path.basename(path)}：{insp['why']}")
            if not SILENT:
                messagebox.showwarning(f"{APP_NAME} · 这不是安装包",
                                       f"{os.path.basename(path)}\n\n{insp['why']}")
            return
        target, mode = installer.plan_target(path, insp["root"])
        self._installing = True
        self._busy_bar(True)
        verb = "就地更新" if mode == "update" else "自动安装"
        self.logline(f"—— {verb} {os.path.basename(path)} → {target} ——")

        def work():
            res = installer.install(
                path, target=target, verify=True,
                progress=lambda f, t: self.after(0, lambda f=f, t=t: self._bar_tick(f, t)),
                log=lambda m: self.after(0, lambda m=m: self.logline(m)),
                report_dir=WORK_DIR)
            self.after(0, lambda r=res: self._install_done(r))

        threading.Thread(target=work, daemon=True).start()

    def _install_done(self, res):
        self._installing = False
        self._bar_tick(1.0 if res.get("ok") else 0.0, "安装完成" if res.get("ok") else "安装失败")
        self.after(600, lambda: self._busy_bar(False))
        if not res.get("ok"):
            self.logline(f"✗ 安装失败：{res.get('why')}")
            if not SILENT:
                messagebox.showerror(f"{APP_NAME} · 安装失败", str(res.get("why")))
            return
        if res.get("report"):
            self.logline(f"安装报告：{res['report']}")
        res.setdefault("bytes", 0)
        res.setdefault("seconds", 0.0)
        res.setdefault("files", res.get("files") or 0)
        write_setup("package", package=os.path.basename(res.get("zip") or ""),
                    version=(res.get("manifest") or {}).get("version"))
        self.logline("已记下：整合包装到位（setup.json → package），下次开不再问「选界面」")
        if SILENT:
            return
        if messagebox.askyesno(f"{APP_NAME} · 装好了",
                               f"已装到：\n{res['target']}\n\n"
                               f"{res.get('files', 0)} 个文件 · "
                               f"{(res.get('bytes') or 0) / 1048576:.2f} MiB · "
                               f"{res.get('seconds') or 0:.1f}s\n\n现在打开这个目录吗？"):
            try:
                os.startfile(res["target"])
            except (AttributeError, OSError):
                pass

    def _poll_drop_inbox(self):
        """第二份启动器（拖到 exe 图标上、或命令行带包的那份）把路径递到 work/drop-inbox/，这里接单。"""
        try:
            pending = installer.take_handoffs(WORK_DIR)
        except Exception:
            pending = []
        for _fp, paths in pending:
            if paths:
                self.logline(f"收到递进来的 {len(paths)} 个包（另一个进程已退出，单实例没破）")
                self.on_drop(paths)
        try:
            self.after(1500, self._poll_drop_inbox)
        except tk.TclError:
            pass

    # ------------------------------------------------------------- 单实例守卫
    def refresh_port(self):
        """端口上是不是已经有人在听：有就说明上一份还在（哪怕不是本壳起的）。"""
        m = self.current_manifest()
        if not m or self._running():
            return
        port = m["port"]

        def work():
            owner = singleton.port_owner(port)
            try:
                self.after(0, lambda: self._apply_port(port, owner))
            except Exception:           # 窗口已销毁（测试里秒关），别在后台线程里炸
                pass
        threading.Thread(target=work, daemon=True).start()

    def _apply_port(self, port, owner):
        if self._running() or not self.winfo_exists():
            return
        if owner:
            self.btn_open.configure(state="normal")
            self._state_pin = time.time() + 6
            self.lbl_state.configure(text=f"⚠ 端口 {port} 被 pid {owner['pid']} 占着",
                                     fg=LEVEL_COLOR[detect.YELLOW])
            self.logline(f"端口 {port} 上已有 pid {owner['pid']} {owner.get('name') or '?'}"
                         f"（不是本壳起的，上次没关干净）—— 启动时会先问要不要停掉它")
        else:
            self.btn_open.configure(state="disabled")

    def _takeover(self, m, owner):
        """端口被占：要么停掉它再起，要么取消（现成的那个点「打开页面」就能用）。"""
        ok = messagebox.askyesno(
            f"{APP_NAME} · 端口 {m['port']} 已被占用",
            f"端口 {m['port']} 上已经有一个进程在跑：\n\n"
            f"  pid {owner['pid']} · {owner.get('name') or '?'}\n"
            f"  {singleton.shorten(owner.get('cmd') or '（拿不到命令行）')}\n\n"
            f"多半是上一次没关干净的内核。\n\n"
            f"【是】先把它（连子进程）停掉，再启动新的\n"
            f"【否】取消 —— 想直接用现成的那个，点「打开页面」")
        if not ok:
            return False
        self.logline(f"接管：停掉端口 {m['port']} 占用者 pid {owner['pid']} …")
        alive = singleton.kill_tree(owner["pid"])
        if alive:
            messagebox.showerror(f"{APP_NAME} · 停不掉",
                                 f"pid {alive} 还在。\n手动：taskkill /PID {alive[0]} /T /F")
            return False
        if not singleton.wait_free(m["port"], 15):
            messagebox.showerror(f"{APP_NAME} · 端口没放",
                                 f"进程退了，但端口 {m['port']} 还在听。\n等十秒再试。")
            return False
        self.logline(f"端口 {m['port']} 已释放")
        self.btn_open.configure(state="disabled")
        self.refresh_detect()
        return True

    def on_close(self):
        """关窗口就把核一起带走 —— 不然就是「端口上挂着一个孤儿」。"""
        if self._running():
            cur = (self.runner.m or {}).get("id", "?") if self.runner else "?"
            if not messagebox.askyesno(
                    f"{APP_NAME} · 还在跑",
                    f"内核 {cur} 还在运行。\n关掉启动器会把它（连子进程）一起停掉，继续？"):
                return
            self.logline(f"退出前停掉内核 {cur} …")
            try:
                clean, alive = self.runner.stop()
                self.logline("已停止：端口释放，无孤儿" if clean
                             else f"✗ 停止不干净，残留 {alive}")
            except Exception as e:
                self.logline(f"✗ 停止出错：{e}")
        self.destroy()

    # ---------------------------------------------------------------- 起停
    def on_start(self):
        if self.busy:
            return
        m = self.current_manifest()
        if not m:
            messagebox.showerror(APP_NAME, "没有可启动的内核。\n请检查 kernels/ 下的 kernel.json。")
            return
        # 规矩一：一个壳只跑一个核 —— 上一个停干净了才放开「启动」
        if self._running():
            cur = (self.runner.m or {}).get("id", "?") if self.runner else "?"
            self.logline(f"忽略启动：内核 {cur} 还在运行（先停止，才能起下一个）")
            messagebox.showwarning(APP_NAME,
                                   f"内核 {cur} 还在运行。\n先点「■ 停止」，停干净了才能起下一个。")
            return
        # 规矩二：端口上已经有进程在听 = 上一份没关干净，问清楚再接管
        owner = singleton.port_owner(m["port"])
        if owner and not self._takeover(m, owner):
            self.logline(f"未启动：端口 {m['port']} 仍被 pid {owner['pid']} 占用")
            return
        self.busy = True
        self.btn_start.configure(state="disabled", bg=ACCENT)
        self.btn_stop.configure(state="normal")
        self.bar.set(0)
        self.progrow.pack(fill="x", pady=(12, 0), before=self.loghead)
        self.lbl_pct.configure(text="0%")
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
        self.bar.set(frac)
        self.lbl_pct.configure(text=f"{frac:.0%}")
        self.lbl_hint.configure(text=text)
        self.title(f"{APP_NAME} {VERSION} · {frac:.0%} {text}")

    def _state(self, state, detail):
        self.lbl_state.configure(
            text=STATE_TEXT.get(state, state),
            fg=LEVEL_COLOR.get(state, self.P["sub"]))
        if state == pipe.GREEN:
            self.title(f"{APP_NAME} {VERSION}")
            self.btn_start.configure(state="disabled", bg=ACCENT)
            self.btn_stop.configure(state="normal")
            self.btn_open.configure(state="normal")
            self.lbl_hint.configure(text="")
            self.bar.set(1.0)
            self.lbl_pct.configure(text="100%")

    def on_stop(self):
        if not self.runner:
            return
        self.btn_stop.configure(state="disabled")
        self.logline("正在停止…")

        def work():
            clean, alive = self.runner.stop()
            self.after(0, lambda: self.logline("已停止：端口释放，无孤儿" if clean
                                               else f"✗ 停止不干净，残留 {alive}"))
            self.after(0, lambda: self.btn_start.configure(state="normal", bg=ACCENT))
            self.after(0, lambda: self.btn_open.configure(state="disabled"))
            self.after(0, lambda: self.bar.set(0))
            self.after(0, lambda: self.lbl_pct.configure(text=""))
            self.after(0, lambda: self.progrow.pack_forget())
            self.after(0, lambda: self.title(f"{APP_NAME} {VERSION}"))
        threading.Thread(target=work, daemon=True).start()

    def on_open(self):
        import webbrowser
        m = self.current_manifest()
        port = m["port"] if m else 8899
        webbrowser.open(f"http://127.0.0.1:{port}/")


def _announce_second_instance():
    """第二个启动器：把已有的窗口叫到前面，然后自己退场（不多开一份）。"""
    lock = singleton.read_lock(os.path.join(WORK_DIR, "launcher.lock"))
    focused = singleton.focus_window(APP_NAME)
    who = f"pid {lock['pid']}（{lock.get('started', '?')} 启动）" if lock else "另一个窗口"
    if os.environ.get("NEOFORGE_SILENT") == "1":   # 自动化/脚本用：只说话不弹窗
        print(f"{APP_NAME} 已在运行：{who}")
        return
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(
            f"{APP_NAME} · 已经在运行",
            f"同一份安装只开一个启动器，已经在跑：{who}。\n\n"
            + ("已经把那个窗口叫到前面了。" if focused else "去任务栏找一下那个窗口。"))
        root.destroy()
    except Exception:
        print(f"{APP_NAME} 已在运行：{who}")


def package_args(argv):
    """命令行里带的包 —— 把 zip 拖到 exe 图标上，Windows 就是把路径当参数递进来。"""
    out = []
    for a in argv:
        if a.startswith("-"):
            continue
        if a.lower().endswith((".zip", ".7z", ".001", ".002", ".rar", ".tar", ".gz")):
            out.append(os.path.abspath(a))
    return out


def main():
    os.makedirs(WORK_DIR, exist_ok=True)
    pending = package_args(sys.argv[1:])
    guard, blocked = None, False
    try:
        guard = singleton.acquire(BASE_DIR,
                                  lock_path=os.path.join(WORK_DIR, "launcher.lock"))
        blocked = guard is None
    except Exception as e:                      # 守卫自己坏了，别把人挡在门外
        print(f"单实例守卫出错（照常启动）：{e}")
    if blocked:
        if pending:                             # 拖到 exe 上的包：交给已经在跑的那份
            installer.handoff(pending, WORK_DIR)
            print(f"{APP_NAME} 已在运行：{len(pending)} 个包已转交给它安装")
            if not SILENT:
                try:
                    root = tk.Tk()
                    root.withdraw()
                    messagebox.showinfo(
                        f"{APP_NAME} · 已转交",
                        "启动器已经在跑，包交过去了：\n\n" + "\n".join(pending) +
                        "\n\n安装进度看那个窗口的日志。")
                    root.destroy()
                except Exception:
                    pass
            return
        _announce_second_instance()
        return
    try:
        app = Launcher()
    except Exception as e:                  # pythonw 双击起时没有控制台，异常要弹窗说人话
        import traceback
        err = traceback.format_exc()
        try:
            os.makedirs(WORK_DIR, exist_ok=True)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(err)
        except OSError:
            pass
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(f"{APP_NAME} 启动失败", f"{e}\n\n详情见 {LOG_FILE}")
        return
    app.guard = guard
    app.logline(f"{APP_NAME} {VERSION} 启动器已就绪（壳不含后端）")
    app.logline("单实例守卫：已取得本安装的启动器所有权" if guard else "单实例守卫：不可用（照常运行）")
    app.logline(f"根目录：{BASE_DIR}")
    app.logline(f"主题：{'sv-ttk dark' if HAS_SVTTK else 'ttk 手配（没装 sv-ttk）'}")
    if "--repair" in sys.argv[1:]:
        app.logline("--repair：启动即跑一次维修（诊断 → 自动修 → 复检），全程不出 cmd 窗口")
        app.after(700, app.on_repair)
    if pending:
        app.logline(f"命令行带来 {len(pending)} 个包，开始自动安装")
        app.after(500, lambda: app.on_drop(pending))
    app.mainloop()
    if guard:
        guard.release()


if __name__ == "__main__":
    main()
