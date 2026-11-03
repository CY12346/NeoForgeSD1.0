# -*- coding: utf-8 -*-
"""「启动器能获得大模型/小模型数据」的守门测试。

链路：魔搭搜索 → 候选条目（名称/类型/体积/下载量）→ 落缓存 → catalog.load 合并
     → hydrate 补出真实文件名与下载 URL。

跑法：E:/NeoForge/backend/python/python.exe app/tests/test_msremote.py
需要网络（魔搭 200 实测可用）；离线时整组跳过并打印 SKIP。
"""
import os
import shutil
import sys
import tempfile

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

from core import catalog as cat          # noqa: E402
from core import msremote as ms         # noqa: E402

FAILED = []


def check(name, cond, extra=""):
    print(("  ✓ " if cond else "  ✗ ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        FAILED.append(name)


def main():
    tmp = tempfile.mkdtemp(prefix="msremote-")
    try:
        print("[1] 魔搭搜索返回可用的条目")
        try:
            got = ms.search("stable-diffusion", page_size=10, sort="downloads")
        except ms.FetchError as e:
            print(f"  SKIP 网络不通：{e}")
            return 0
        check("search 有结果", len(got) >= 5, f"n={len(got)}")
        check("每条都有 id/repo/name/subtype", all(
            it.get("id") and it.get("repo") and it.get("name") and it.get("subtype") for it in got))
        check("每条都带体积", all(it.get("size") for it in got))
        kinds = {it["subtype"] for it in got}
        print("    类型分布：", ", ".join(sorted(kinds)))

        print("[2] 搜索结果能落缓存、catalog.load 能读回来")
        os.makedirs(os.path.join(tmp, "work"), exist_ok=True)
        ms.save_cache(tmp, got)
        merged = cat.load(tmp)
        check("catalog.load 合并了在线条目",
              len(merged) >= len(got), f"内置+在线={len(merged)} 在线={len(got)}")
        got_ids = {it["id"] for it in got}
        check("在线条目都在合并结果里", got_ids <= {it["id"] for it in merged})
        check("每条都有可显示的类型", all(cat.subtype_of(it) for it in merged))

        print("[3] 选中一条 → hydrate 出真实文件与下载 URL")
        # 拿一个确定的 SD1.5 底模仓库走完整链路（搜索出来的第一名可能是 GGUF 之类 WebUI 用不了的）
        cand = {"id": "ms-MusePublic_majicMIX_realistic_maijuxieshi_SD_1_5",
                "repo": "MusePublic/majicMIX_realistic_maijuxieshi_SD_1_5",
                "name": "majicMIX realistic", "subtype": "底模 · SD1.5", "kind": "model-big",
                "size": 0, "urls": [], "file": "models/Stable-diffusion/majicMIX.safetensors",
                "source": "modelscope"}
        full = ms.hydrate(cand)
        check("hydrate 拿到了下载链接", bool(full.get("urls")), str(full.get("urls"))[:90])
        check("hydrate 拿到了落点", full.get("file", "").startswith("models/"), full.get("file", ""))
        check("hydrate 修正了体积", (full.get("size") or 0) > 100, f"{full.get('size')} MiB")

        gguf = {"id": "x", "repo": "gpustack/stable-diffusion-v3-5-large-GGUF",
                "urls": [], "file": "models/Stable-diffusion/x.safetensors", "size": 0}
        h = ms.hydrate(gguf)
        check("没有 WebUI 权重时 hydrate 不崩、不瞎编 URL",
              not h.get("urls") and h.get("repo") == gguf["repo"])

        print("[4] 类型判定（库标注不可靠，靠名字/体积兜住）")
        cases = [
            (["custom_tag:Checkpoint", "library:lora", "custom_tag:text-to-image"],
             "majicMIX realistic", 2_000_000_000, "底模"),
            (["custom_tag:LoRA", "library:lora"], "polaroid_lora", 150_000_000, "LoRA"),
            (["custom_tag:VAE"], "flux_vae", 300_000_000, "VAE"),
            (["custom_tag:Checkpoint"], "Realistic_Vision_V5.1_noVAE", 9_000_000_000, "底模"),
        ]
        for tags, name, size, want in cases:
            got_sub = ms.type_from_tags(tags, name, size)
            check(f"{name[:26]:26s} → {want}", got_sub.startswith(want), f"实际 {got_sub}")

        print("[5] 链接解析")
        check("URL 解析", ms.parse_ref("https://www.modelscope.cn/models/AI-ModelScope/stable-diffusion-v1-5")
              == "AI-ModelScope/stable-diffusion-v1-5")
        check("owner/name 解析", ms.parse_ref(" AI-ModelScope/stable-diffusion-v1-5 ")
              == "AI-ModelScope/stable-diffusion-v1-5")
        check("乱码不解析", ms.parse_ref("随便写点什么") is None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILED:
        print(f"失败 {len(FAILED)} 项：" + "，".join(FAILED))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
