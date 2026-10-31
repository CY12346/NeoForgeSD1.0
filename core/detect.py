# -*- coding: utf-8 -*-
"""环境侦探：只报事实，不猜、不兜底。

每项检查返回 {key, label, level, detail, fix}：
  level = green（能用） / yellow（能跑但受限） / red（缺东西，跑不了）
  fix   = 一句人话 + 一个动作，red/yellow 必填
"""
import os
import shutil
import socket
import subprocess

GREEN, YELLOW, RED = "green", "yellow", "red"


def _item(key, label, level, detail, fix=""):
    return {"key": key, "label": label, "level": level, "detail": detail, "fix": fix}


# ------------------------------------------------------------------ 单项检查
def check_python(base):
    py = os.path.join(base, "backend", "python", "python.exe")
    if os.path.isfile(py):
        try:
            out = subprocess.run([py, "-c", "import sys;print(sys.version.split()[0])"],
                                 capture_output=True, timeout=20)
            ver = out.stdout.decode("utf-8", "replace").strip() or "?"
            return _item("python", "后端 Python", GREEN, f"{ver} · {py}")
        except Exception as e:
            return _item("python", "后端 Python", YELLOW, f"存在但跑不起来：{e}",
                         "重装 backend/python，或确认没被杀软隔离")
    return _item("python", "后端 Python", RED, f"缺少 {py}",
                 "把 backend 目录放到启动器同级；1.3.0 起内核需要通过「安装引导」装")


def check_models(base):
    md = os.path.join(base, "models", "Stable-diffusion")
    if not os.path.isdir(md):
        return _item("models", "模型目录", RED, f"缺少 {md}",
                     "把已有的 models 目录移到启动器同级，或用「安装引导」拉取")
    ckpts = []
    for dirpath, _d, files in os.walk(md):
        for f in files:
            if f.lower().endswith((".safetensors", ".ckpt")):
                ckpts.append(os.path.join(dirpath, f))
        if len(ckpts) >= 3:
            break
    if not ckpts:
        return _item("models", "基础模型", RED, f"{md} 里没有 .safetensors/.ckpt",
                     "往 models/Stable-diffusion/ 放一个底模（如 AWPainting v1.2）")
    return _item("models", "基础模型", GREEN, f"{len(ckpts)} 个 · 例如 {os.path.basename(ckpts[0])}")


def check_port(_base=None, port=8899):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        busy = s.connect_ex(("127.0.0.1", port)) == 0
    if busy:
        return _item("port", f"端口 {port}", YELLOW, "已被占用",
                     f"要么它就是在跑（直接打开页面），要么换内核端口：改 kernels/*/kernel.json 的 port")
    return _item("port", f"端口 {port}", GREEN, "空闲")


def check_disk(base=None, min_gb=5):
    target = base or os.path.abspath(os.sep)
    try:
        free_gb = shutil.disk_usage(target).free / 1073741824
    except Exception as e:
        return _item("disk", "磁盘余量", YELLOW, f"读不到：{e}", "手动看下盘还剩多少")
    if free_gb < min_gb:
        return _item("disk", "磁盘余量", RED, f"仅剩 {free_gb:.1f} GB",
                     f"清到至少 {min_gb} GB：模型与输出最容易把盘塞满")
    return _item("disk", "磁盘余量", GREEN, f"{free_gb:.1f} GB 可用")


def check_gpu(_base=None, need_gb=4):
    exe = shutil.which("nvidia-smi")
    if not exe:
        return _item("gpu", "显卡", YELLOW, "没找到 nvidia-smi",
                     "没有 N 卡也能跑（CPU 出图极慢），或用 --use-cpu 模式验证流程")
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, timeout=20)
        line = out.stdout.decode("utf-8", "replace").strip().splitlines()
        if not line:
            return _item("gpu", "显卡", YELLOW, "nvidia-smi 无输出", "更新显卡驱动后重试")
        name, total, used = [x.strip() for x in line[0].split(",")]
        total_gb, used_gb = float(total) / 1024, float(used) / 1024
        free_gb = total_gb - used_gb
        detail = f"{name} · 共 {total_gb:.1f} GB，已用 {used_gb:.1f} GB"
        if total_gb < need_gb:
            return _item("gpu", "显卡", YELLOW, detail,
                         f"显存低于 {need_gb} GB：用 512×512 出图，别开高清修复")
        if free_gb < need_gb * 0.8:
            return _item("gpu", "显卡", YELLOW, detail + "（余量偏紧）",
                         "关掉别的显存大户（浏览器硬件加速、游戏）再启动")
        return _item("gpu", "显卡", GREEN, detail)
    except Exception as e:
        return _item("gpu", "显卡", YELLOW, f"读不到：{e}", "更新显卡驱动后重试")


def check_kernels(base):
    candidates = [os.path.join(base, "kernels"), os.path.join(base, "app", "kernels")]
    sys_path = next((c for c in candidates if os.path.isdir(c)), None)
    if not sys_path:
        return _item("kernels", "内核登记册", RED, "没有 kernels/ 目录",
                     "1.3.0 起内核靠 kernels/<名>/kernel.json 登记；至少放一个")
    ids = [d for d in sorted(os.listdir(sys_path))
           if os.path.isfile(os.path.join(sys_path, d, "kernel.json"))]
    if not ids:
        return _item("kernels", "内核登记册", RED, "kernels/ 下没有任何 kernel.json",
                     "加一个内核目录，或先用 kernels/fake 验证管道")
    return _item("kernels", "内核登记册", GREEN, f"{len(ids)} 个：{', '.join(ids)}")


DEFAULT_CHECKS = (check_python, check_models, check_port, check_disk, check_gpu, check_kernels)


def run_checks(base, checks=None):
    """跑全部检查。checks 可替换单项（测试用：造故障）。"""
    out = []
    for fn in (checks or DEFAULT_CHECKS):
        try:
            out.append(fn(base))
        except Exception as e:  # 侦探自己不能把启动器带崩
            out.append(_item(getattr(fn, "__name__", "check"), "检查出错", YELLOW, str(e),
                             "这项检查自身报错，先跳过；其余照旧"))
    return out


def verdict(items):
    """(最坏级别, 一句话总结)。"""
    order = {RED: 2, YELLOW: 1, GREEN: 0}
    worst = max(items, key=lambda i: order[i["level"]]) if items else None
    if not worst:
        return GREEN, "没有可检查项"
    bad = [i for i in items if i["level"] != GREEN]
    if not bad:
        return GREEN, "全部就绪"
    return worst["level"], "；".join(f"{i['label']}：{i['detail']}" for i in bad[:3])
