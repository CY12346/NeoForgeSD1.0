# -*- coding: utf-8 -*-
"""下载目录（catalog）：能下什么、从哪下、落到哪。

原则：**只收录本机实测能下通的源**（2026-10-31 逐个验过）。github.com 直连在本机是死的
（443 被 reset），所以：源码包走 codeload、模型走 ModelScope、release 附件跟随跳转走
release-assets.githubusercontent.com —— 这三条都真下过字节。

用户可以放一份 `work/catalog.json` 盖掉内置目录（同样的 schema），启动器优先读它。
"""
import json
import os

MIB = 1048576

# 种类 -> 中文名（界面上的分类）
KINDS = {
    "model-big": "大模型",
    "model-small": "小模型",
    "extension": "插件",
    "ui": "UI 本体",
}
KIND_ORDER = ("model-big", "model-small", "extension", "ui")

MS = "https://modelscope.cn/api/v1/models/{repo}/repo?Revision=master&FilePath={path}"
CL = "https://codeload.github.com/{repo}/zip/refs/heads/{branch}"
GH = "https://github.com/{repo}/releases/download/{tag}/{file}"


def _ms(repo, path):
    return MS.format(repo=repo, path=path)


def _cl(repo, branch="master"):
    return CL.format(repo=repo, branch=branch)


def _gh(repo, tag, file):
    return GH.format(repo=repo, tag=tag, file=file)


# 每项：id / kind / name / desc / size(MiB，None=未知) / urls / file(相对 base) /
#       archive(下完要不要解包) / unzip_to(解到哪) / strip_top(去掉仓库 zip 的顶层目录) / kernel(下完顺手登记成内核)
ITEMS = [
    # ---------------------------------------------------------------- 大模型
    {
        "id": "sd15-emaonly",
        "kind": "model-big",
        "name": "SD1.5 底模 · v1-5-pruned-emaonly",
        "desc": "4 GB 卡的主力底模：512² 约 5.7 s、1024² 约 37.7 s（本机实测）。先下这个。",
        "size": 4067.6,
        "urls": [_ms("AI-ModelScope/stable-diffusion-v1-5", "v1-5-pruned-emaonly.safetensors")],
        "file": "models/Stable-diffusion/v1-5-pruned-emaonly.safetensors",
    },
    {
        "id": "sdxl-base",
        "kind": "model-big",
        "name": "SDXL 底模 · sd_xl_base_1.0",
        "desc": "画质跨代，但 4 GB 卡 20 步约 7.3 分钟/张；还得配 SDXL 专用 VAE。适合精修单张。",
        "size": 6616.7,
        "urls": [_ms("AI-ModelScope/stable-diffusion-xl-base-1.0", "sd_xl_base_1.0.safetensors")],
        "file": "models/Stable-diffusion/sd_xl_base_1.0.safetensors",
    },
    # ---------------------------------------------------------------- 小模型
    {
        "id": "vae-ft-mse",
        "kind": "model-small",
        "name": "VAE · vae-ft-mse-840000-ema-pruned",
        "desc": "SD1.5 通用 VAE，出图不发灰。外壳会自动挑 VAE，但自己放一份更稳。",
        "size": 319.1,
        "urls": [_ms("AI-ModelScope/sd-vae-ft-mse-original", "vae-ft-mse-840000-ema-pruned.safetensors")],
        "file": "models/VAE/vae-ft-mse-840000-ema-pruned.safetensors",
    },
    {
        "id": "realesrgan-x4plus",
        "kind": "model-small",
        "name": "放大 · RealESRGAN_x4plus",
        "desc": "4 倍放大，放大/后期组离线可用（A1111 缺了会去 github 现下，基本下不动）。",
        "size": 63.9,
        "urls": [_ms("AI-ModelScope/RealESRGAN_x4plus", "RealESRGAN_x4plus.pth")],
        "file": "models/ESRGAN/RealESRGAN_x4plus.pth",
    },
    {
        "id": "gfpgan-v14",
        "kind": "model-small",
        "name": "修脸 · GFPGAN v1.4",
        "desc": "A1111 的「修复面部」用它；64 MB 级之外还要 detection/parsing 两个小件（首次运行自下）。",
        "size": 348.0,
        "urls": [_gh("TencentARC/GFPGAN", "v1.3.0", "GFPGANv1.4.pth")],
        "file": "models/GFPGAN/GFPGANv1.4.pth",
    },
    {
        "id": "codeformer",
        "kind": "model-small",
        "name": "修脸 · CodeFormer",
        "desc": "另一种修脸算法，比 GFPGAN 更能救模糊脸，代价是慢一点。",
        "size": 359.0,
        "urls": [_gh("sczhou/CodeFormer", "v0.1.0", "codeformer.pth")],
        "file": "models/Codeformer/codeformer-v0.1.0.pth",
    },
    # ---------------------------------------------------------------- 插件
    {
        "id": "ext-adetailer",
        "kind": "extension",
        "name": "插件 · ADetailer（自动修脸/修手）",
        "desc": "出图后自动二次修脸，4 GB 卡实测约 9 s/张，是这台机器最值的一个插件。",
        "size": None,
        "urls": [_cl("Bing-su/adetailer", "main")],
        "file": "work/downloads/ext-adetailer.zip",
        "archive": True,
        "unzip_to": "backend/extensions/adetailer",
        "strip_top": True,
    },
    {
        "id": "ext-prompt-all-in-one",
        "kind": "extension",
        "name": "插件 · 提示词补全（中文）",
        "desc": "中文提示词翻译/补全，外壳的提示词助手也吃它的词典。",
        "size": None,
        "urls": [_cl("Physton/sd-webui-prompt-all-in-one", "main")],
        "file": "work/downloads/ext-prompt-all-in-one.zip",
        "archive": True,
        "unzip_to": "backend/extensions/sd-webui-prompt-all-in-one",
        "strip_top": True,
    },
    {
        "id": "ext-tagcomplete",
        "kind": "extension",
        "name": "插件 · 标签自动补全",
        "desc": "Danbooru 标签补全，写 tag 快一半。",
        "size": None,
        "urls": [_cl("DominikDoom/a1111-sd-webui-tagcomplete", "main")],
        "file": "work/downloads/ext-tagcomplete.zip",
        "archive": True,
        "unzip_to": "backend/extensions/a1111-sd-webui-tagcomplete",
        "strip_top": True,
    },
    {
        "id": "ext-zh-hans",
        "kind": "extension",
        "name": "插件 · 简体中文界面",
        "desc": "A1111 界面汉化。",
        "size": None,
        "urls": [_cl("hanamizuki-ai/stable-diffusion-webui-localization-zh_Hans", "master")],
        "file": "work/downloads/ext-zh-hans.zip",
        "archive": True,
        "unzip_to": "backend/extensions/stable-diffusion-webui-localization-zh_Hans",
        "strip_top": True,
    },
    {
        "id": "ext-controlnet",
        "kind": "extension",
        "name": "插件 · ControlNet",
        "desc": "姿态/线稿/深度控制。注意：模型权重另算，且 4 GB 卡开 ControlNet 要降分辨率。",
        "size": None,
        "urls": [_cl("Mikubill/sd-webui-controlnet", "main")],
        "file": "work/downloads/ext-controlnet.zip",
        "archive": True,
        "unzip_to": "backend/extensions/sd-webui-controlnet",
        "strip_top": True,
    },
    # ---------------------------------------------------------------- UI 本体（官方整合包，自带 python+torch）
    {
        "id": "ui-forge",
        "kind": "ui",
        "name": "WebUI · Forge 官方整合包",
        "desc": "WebUI 系的主力分支（lllyasviel/stable-diffusion-webui-forge），官方 release 整合包，"
                "自带 python 与 torch，解完就能起；A1111 插件基本能用。1804 MiB。"
                "⚠ 本机实测：表头通、支持 Range，但整包传输被掐（curl 0 KiB/s）——"
                "下不动就用浏览器下完，把 .7z 拖进启动器，一样能装。",
        "size": 1803.0,
        "urls": [_gh("lllyasviel/stable-diffusion-webui-forge", "latest",
                     "webui_forge_cu124_torch24.7z")],
        "file": "work/downloads/ui-forge.7z",
        "archive": True,
        "sevenzip": True,
        "unzip_to": "kernels/forge",
        "strip_top": True,
        "kernel": {"id": "forge", "name": "WebUI · Forge", "version": "downloaded",
                   "dialect": "a1111", "auto": True, "port": 7861,
                   "health": {"path": "/", "interval_s": 2.0, "timeout_s": 3.0, "fail_threshold": 3},
                   "ready": {"mode": "poll"}, "outputs": "{kernel}/outputs",
                   "notes": "启动器下载的 Forge 官方整合包（自带 python+torch）。"},
    },
    {
        "id": "ui-comfyui",
        "kind": "ui",
        "name": "ComfyUI · 官方 portable 整合包",
        "desc": "Comfy-Org 官方 portable（nvidia 版，自带 python+torch），解完就是能跑的工作流后端；1945 MiB。"
                "⚠ 本机实测：表头通、支持 Range，整包同样被限速——下不动就用浏览器下完，拖进启动器。",
        "size": 1945.0,
        "urls": [_gh("Comfy-Org/ComfyUI", "v0.43.0", "ComfyUI_windows_portable_nvidia.7z")],
        "file": "work/downloads/ui-comfyui.7z",
        "archive": True,
        "sevenzip": True,
        "unzip_to": "kernels/comfyui",
        "strip_top": True,
        "kernel": {"id": "comfyui", "name": "ComfyUI · 工作流后端", "version": "downloaded",
                   "dialect": "comfy", "auto": True, "port": 8188,
                   "health": {"path": "/system_stats", "interval_s": 2.0, "timeout_s": 3.0,
                              "fail_threshold": 3},
                   "ready": {"mode": "poll"}, "outputs": "{kernel}/output",
                   "notes": "启动器下载的 ComfyUI 官方 portable（自带 python+torch）。"},
    },
    {
        "id": "ui-a1111",
        "kind": "ui",
        "name": "A1111 WebUI（老牌，源码）",
        "desc": "原作者那份仓库还在更新（2026-03 有提交），但生态正往 Forge 迁。"
                "给的是源码 zip，要自己准备 python+torch；本机已有 backend/python 可直接复用。",
        "size": None,
        "urls": [_cl("AUTOMATIC1111/stable-diffusion-webui", "master")],
        "file": "work/downloads/ui-a1111.zip",
        "archive": True,
        "unzip_to": "kernels/a1111-src",
        "strip_top": True,
    },
]

# 引导页上两张卡片的话（优劣是真跑出来 + 踩坑记录，不是抄宣传语）
UI_CHOICE_TEXT = {
    "forge": {
        "title": "WebUI（Forge）",
        "tagline": "WebUI 这条线的活跃分支，官方整合包自带环境",
        "pros": [
            "WebUI 系现在的主力分支（lllyasviel/stable-diffusion-webui-forge），"
            "A1111 的插件 ADetailer / ControlNet / 中文补全基本都能直接用",
            "官方 release 直接给整合包，自带 python 与 torch —— 解完就能起，不用自己配环境",
            "界面就是熟悉的那张 WebUI，比 ComfyUI 上手快",
            "这条线上跑出来的老数字可以当参考：4 GB 卡 512² 5.7 s · 1024² 37.7 s（A1111 实测）",
        ],
        "cons": [
            "我们还没在这台机器上实测 Forge，4 GB 卡的最优参数要自己再调一遍",
            "整合包 1.8 GB：下载 + 解包要等，首次启动还要装依赖，慢",
            "插件虽兼容，个别扩展在 Forge 上会报错，得自己排查",
        ],
        "pick": "想要「熟悉的 WebUI 界面 + 自带环境、解完就能起」—— 选这个。",
    },
    "comfyui": {
        "title": "ComfyUI",
        "tagline": "工作流自由，显存更省",
        "pros": [
            "官方 portable 整合包同样自带 python 与 torch，解完就能起（1.9 GB）",
            "按工作流搭，出图链路可以精确控制、可复用",
            "显存占用比 WebUI 低，新模型（SDXL / Flux）跟进快",
            "跑批量、跑多阶段（先粗后修）比 WebUI 干净",
        ],
        "cons": [
            "没有现成的中文界面，也没有 ADetailer 那种一键修脸（要自己搭节点）",
            "要自己存/管工作流文件，学习成本明显更高",
            "本机同样没实测过 ComfyUI 在 4 GB 卡上的参数",
        ],
        "pick": "愿意花时间搭流程、想省显存 —— 选这个。",
    },
}


def load(base_dir=None):
    """读目录：优先 work/catalog.json（用户自己改的），否则内置；再叠上在线清单。

    在线清单（work/catalog-online.json）由 core/msremote.py 从 ModelScope 解析出来，
    跟内置条目合并去重 —— 内置的稳，在线的多。
    """
    items = None
    if base_dir:
        custom = os.path.join(base_dir, "work", "catalog.json")
        if os.path.isfile(custom):
            try:
                with open(custom, encoding="utf-8") as f:
                    data = json.load(f)
                got = data.get("items") if isinstance(data, dict) else data
                if got:
                    items = got
            except (OSError, ValueError):
                pass
    if items is None:
        items = [dict(it) for it in ITEMS]
    else:
        items = list(items)

    if base_dir:
        try:
            from core import msremote            # 懒导入：壳缺件也不该拖死下载页
            have = {it.get("id") for it in items}
            items.extend(it for it in msremote.load_cache(base_dir) if it.get("id") not in have)
        except Exception:                        # noqa: BLE001  在线清单坏了就当没有
            pass
    return with_stars(items, base_dir)


# 内置热度（2026-11-04 从 GitHub API 实测；work/popularity.json 里的在线值会盖掉它）
# 只有插件和 UI 有 —— 魔搭那边没有 stars，只有下载量，两个口径不混着排
STARS_BY_ID = {
    "ui-a1111": 165236,
    "ui-comfyui": 144825,
    "ext-controlnet": 17815,
    "ui-forge": 13111,
    "ext-adetailer": 4798,
    "ext-prompt-all-in-one": 3241,
    "ext-tagcomplete": 2814,
    "ext-zh-hans": 479,
}


def with_stars(items, base_dir=None):
    """给插件/UI 条目补 stars：在线缓存优先，内置实测值兜底。"""
    live = {}
    if base_dir:
        try:
            from core import popularity
            live = popularity.by_id(base_dir)
        except Exception:                        # noqa: BLE001
            pass
    for it in items:
        if it.get("stars"):
            continue
        s = live.get(it.get("id")) or STARS_BY_ID.get(it.get("id"))
        if s:
            it["stars"] = s
    return items



# 界面上「类型」列用的细分标签（比 kind 的四分类有用：一眼看出是底模还是 LoRA）
SUBTYPE_BY_ID = {
    "sd15-emaonly": "底模 · SD1.5",
    "sdxl-base": "底模 · SDXL",
    "vae-ft-mse": "VAE",
    "realesrgan-x4plus": "放大",
    "gfpgan-v14": "修脸",
    "codeformer": "修脸",
    "ext-adetailer": "插件 · 修脸",
    "ext-prompt-all-in-one": "插件 · 提示词",
    "ext-tagcomplete": "插件 · 提示词",
    "ext-zh-hans": "插件 · 汉化",
    "ext-controlnet": "插件 · 控制",
    "ui-forge": "UI 本体",
    "ui-comfyui": "UI 本体",
    "ui-a1111": "UI 本体",
}


def subtype_of(it):
    return (it.get("subtype") or SUBTYPE_BY_ID.get(it.get("id"))
            or KINDS.get(it.get("kind")) or it.get("kind") or "")



def by_id(items=None, base_dir=None):
    return {it["id"]: it for it in (items or load(base_dir))}


def grouped(items=None, base_dir=None):
    """按种类分组，界面直接铺。"""
    items = items or load(base_dir)
    out = {k: [] for k in KIND_ORDER}
    for it in items:
        out.setdefault(it.get("kind", "other"), []).append(it)
    return out


def find(items, item_id):
    return next((it for it in items if it["id"] == item_id), None)
