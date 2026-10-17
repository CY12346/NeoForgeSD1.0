# NeoForgeSD 1.0

本地 Stable Diffusion **出图编排层**：双后端客户端 + 批量出图调度器 + MCP 桥。

> 这不是 Forge 或 ComfyUI 的分支。NeoForgeSD 是一层**独立于引擎之上**的编排代码，通过 HTTP API 驱动你已经装好的 Forge WebUI / ComfyUI，把「镜头表」变成「一批落盘的图」。它**不含任何模型权重**，模型由你自己下载并遵守其各自许可。

---

## 它能干什么

一条命令，把一整集的分镜镜头表批量出图，逐镜计时、记录 seed、命名落盘、失败不中断：

```bash
python manhua.py --shots shots.json --backend comfy --out out_dir --limit 10
```

三块组成：

| 模块 | 文件 | 作用 |
|---|---|---|
| 双后端客户端 | `core/comfy_client.py` `core/sdclient.py` | 封装 ComfyUI(`:8188`) 与 Forge WebUI(`:7860`) 的 HTTP API，把实测踩过的坑固化进去 |
| 出图调度器 | `manhua.py` | 镜头表 → 批量出图 → 命名落盘 + manifest.json |
| MCP 桥 | `bridge/` | 把本机 AIGC 能力（SD 出图 / ComfyUI / ffmpeg / Blender / 语音）暴露成 MCP 工具给 AI Agent |

---

## 架构

```
镜头表 shots.json
      │
      ▼
  manhua.py  ──┬── ComfyBackend ──► core/comfy_client.py ──► ComfyUI :8188  (DiT 模型，如 Anima)
               └── ForgeBackend ──► core/sdclient.py    ──► Forge   :7860  (SD1.5/SDXL/Illustrious)
      │
      ▼
  一批 PNG + manifest.json（逐镜 seed / 耗时 / 路径）
```

上层只认 `generate(shot) -> [png bytes]` 一个接口，换引擎不动业务代码。

---

## 快速开始

### 1. 出图（调度器）

```bash
# 镜头表（JSON）
{
  "backend": {
    "comfy": {
      "workflow": "path/to/api-format-workflow.json",
      "nodes": {"unet":"44","clip":"45","vae":"15","positive":"11","negative":"12","sampler":"19","latent":"28"},
      "unet": "anima-turbo-v1.1.safetensors",
      "clip": "qwen_3_06b_base.safetensors",
      "vae": "qwen_image_vae.safetensors"
    }
  },
  "defaults": {"width":768, "height":1344, "steps":8, "cfg":1.5, "sampler":"er_sde", "scheduler":"simple", "seed":-1},
  "shots": [
    {"id":"s01", "prompt":"anime boy, flower shop counter, warm afternoon light"},
    {"id":"s02", "prompt":"anime girl, denim shorts, standing outdoors", "steps":12}
  ]
}
```

```bash
python manhua.py --shots shots.json --backend comfy --out out_dir --limit 5 --dry-run  # 先空跑
python manhua.py --shots shots.json --backend comfy --out out_dir                     # 正式出图
```

### 2. MCP 桥（给 Agent 用）

```bash
cd bridge && npm install
node server.mjs   # 走 stdio JSON-RPC，由 MCP 客户端拉起
```

工具一览：`image_generate` / `image_edit` / `image_upscale` / `comfy_generate` / `video_generate` / `video_ffmpeg` / `tts_speak` / `voice_clone` / `blender_run` 等。

---

## 兼容后端

| 后端 | 地址 | 适用模型 | 备注 |
|---|---|---|---|
| ComfyUI 0.41.0 | `:8188` | DiT 模型（Anima 等 Forge 加载不了的） | 需 torch ≥ 2.8 才开 DynamicVRAM |
| Forge WebUI | `:7860` | SD1.5 / SDXL / Illustrious | 采样器名**不带** `Karras` 后缀，调度器单独传 |

### 3. 4GB 显存跑 SDXL：fp8 拆分（降显存）

SDXL 全家桶 checkpoint（~6.9G）直接加载，权重显存是大头。拆成 UNet + 双 CLIP + VAE 后按需选择精度：

```bash
# 用 ComfyUI 自带的 venv（有 torch2.8 + safetensors）
D:\AIGC\comfyui\venv\Scripts\python.exe tools/checkpoint_split_fp8.py <checkpoint.safetensors> <comfyui根目录>
```

产出 `diffusion_models/<stem>-fp16.safetensors`（默认用这个）+ `-fp8`（兜底）+ `text_encoders/*_clip_l/_clip_g` + `vae/*_vae`。

> **实测结论（4GB 卡，DynamicVRAM 开启，768x1344/25步）**：fp8 峰值显存只省 0.14G（3.45 vs 3.59G）、速度持平，但同 seed 像素差 rms≈27.6（PSNR 19.3dB）、平坦区噪点 1.62 倍 → **默认选 fp16 + DynamicVRAM**（本机可稳定跑，峰值 ~3.5G 不爆）；fp8 仅当关 DynamicVRAM 或显存被 LoRA 挤占时兜底。

---

## ⚠️ 模型许可（务必自审）

**本仓库只含代码，不含任何模型权重。** 模型由你从 ModelScope / Hugging Face 自行下载，并遵守其各自许可。以下是几个常见底模的许可现状（截至 2026-10，供参考，最终以官方 LICENSE 为准）：

| 底模 | 许可 | 商用出片 |
|---|---|---|
| Anima 1.1（CircleStone Labs） | CircleStone **非商用**许可 v1.2 | ❌ 商用/生产用途都踩线 |
| NoobAI-XL | FAIPL-1.0-SD（带商用禁令） | ❌ |
| **Illustrious-XL v2.0** | **CreativeML OpenRAIL++-M** | ✅ 官方明确可商用 |
| Illustrious-XL v0.1 | FAIPL-1.0-SD | ⚠️ 输出商用可，衍生模型须开源 |
| SD1.5 / SDXL 基座 | OpenRAIL-M / OpenRAIL++-M | ✅ |

**要点**：Anima 这类「非商用 + 非生产用途」许可的模型，即使是免费发到公开平台（B 站、抖音等，无论是否开充电/打赏），只要属于公开发布 + 平台可产生收入，就大概率构成「生产/商用」用途。要变现就用 Illustrious-XL v2.0 或 SD1.5/SDXL 这类 OpenRAIL 许可的底模。

---

## 目录

```
manhua.py            # 出图调度器（入口）
core/                # 双后端 HTTP 客户端
bridge/              # MCP 桥（node，含 py/ 辅助脚本）
tools/               # 模型下载 / checkpoint 拆分(fp8) / ComfyUI 自检等小工具
workflows/           # 现成工作流模板（SDXL 竖屏 fp8 等）
spike/               # 实测/基准脚本（选型依据留痕）
```

## License

MIT — 仅针对本仓库代码。模型权重不属于本仓库，遵循其各自许可。
