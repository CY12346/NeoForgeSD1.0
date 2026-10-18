# NeoForgeSD 1.0

本地 Stable Diffusion **出图编排层**：双后端客户端 + 批量出图调度器 + MCP 桥。

> 这不是 Forge 或 ComfyUI 的分支。NeoForgeSD 是一层**独立于引擎之上**的编排代码，通过 HTTP API 驱动你已经装好的 NeoForge WebUI（Forge Classic 兼容）/ ComfyUI，把「镜头表」变成「一批落盘的图」。它**不含任何模型权重**，模型由你自己下载并遵守其各自许可。

---

## 它能干什么

一条命令，把一整集的分镜镜头表批量出图，逐镜计时、记录 seed、命名落盘、失败不中断：

```bash
python manhua.py --shots shots.json --backend comfy --out out_dir --limit 10
```

三块组成：

| 模块 | 文件 | 作用 |
|---|---|---|
| 双后端客户端 | `core/comfy_client.py` `core/sdclient.py` | 封装 ComfyUI(`:8188`) 与 NeoForge WebUI(`:7860`) 的 HTTP API，把实测踩过的坑固化进去 |
| 出图调度器 | `manhua.py` | 镜头表 → 批量出图 → 命名落盘 + manifest.json |
| MCP 桥 | `bridge/` | 把本机 AIGC 能力（SD 出图 / ComfyUI / ffmpeg / Blender / 语音）暴露成 MCP 工具给 AI Agent |

---

## 架构

```
镜头表 shots.json
      │
      ▼
  manhua.py  ──┬── ComfyBackend ──► core/comfy_client.py ──► ComfyUI :8188  (DiT 模型，如 Anima)
               └── NeoForgeBackend ──► core/sdclient.py ──► NeoForge :7860 (SD1.5/SDXL/Illustrious)
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

## 动作（视频）与图形入口

出图之后最常问的是「让人物动起来」。本机实测给出两条引擎，别混用：

| 引擎 | 显存 | 3 秒成片耗时 | 能做什么 | 不能做什么 |
|---|---|---|---|---|
| **puppet**（立绘形变场） | 0（纯 CPU） | 10–16 s @720×720/24fps | 招手 / 举双手 / 点头 / 跳跃 / 伸手 / 出拳 / 鞠躬 / 待机，动作由曲线决定，**不漂移、不糊尾帧** | 需要单张正面立绘；大幅肢体会有一点「木偶感」 |
| **animatediff**（SD 图生视频） | ≈3.9 G（4G 卡满负荷） | 457 s @512×512/24 帧 | 呼吸、衣角、发丝、背景微动，氛围感 | 剧本级动作（抬手/挥拳）几乎不动；denoise ≥0.6 掉身份，后段还会糊 |

### 命令行

```bash
python -m core.motion --action list                      # 全部动作模板
python -m core.motion --sprite hero.png --action wave \
    --dur 3 --fps 24 --size 720x720 --bg assets/bg_cornfield.png \
    --out out/wave.mp4 --metrics                          # 出片并做连贯性体检
```

### 连贯性体检（`core/quality.py`）

一条视频算三组指标，给出 `COHERENT / NEEDS_WORK`：
运动量 `diff_subject`（只在「整段里变过的像素」上算差分，不受无背景/主体占比小的稀释；同时保留整帧 `diff_mean` 作对照）、流畅度 `diff_cv` + `max_jerk`（跳变尖峰）、稳定度 `duplicate_frames` / `color_drift_end` / `flicker_pct`。
命令行：`python core/quality.py out/wave.mp4`。

本机 8 个动作模板实测全部 `COHERENT`（diff_subject 2.1–3.3、cv ≤0.41、0 重复帧、闪烁 ≤1.3%）；无背景纯色画布同样判 COHERENT（整帧口径会误判 —— 同一支 nod：diff_mean 0.27 / diff_subject 2.11）。重复帧按 5% 容忍（动作收尾的定格帧不算冻结），超过才判 freeze；纯静态片仍是 `NEEDS_WORK`。

### 轨迹驱动（`core/traj.py`）

8 个模板是固定动作；要让木偶照着某支现成片子打拳/挥手，就用轨迹驱动——先从片子里把运动抽成**事件**，再用事件驱动形变（不是逐帧抠动作，所以不会漂移）。

```bash
# ① 抽轨迹：视频给路径，也可以直接给一帧目录（png 序列）
python -m core.traj extract D:/ref/punch.gif --out traj.json --fps 10 --mode skin
#   motion（默认）：帧差前景，任何主体都能用（走/跳/挥）
#   skin          ：肤色 prior + 静态肤色剔除，专治「手和脸肤色一样、帧差糊成一团」

# ② 出片：事件 → 冲量曲线（0.05s 起手 / 0.15s 指数衰减）驱动抬臂·倾斜·挤压·前倾，
#    再叠冲击线与递减抖动；出片自动跑连贯性体检
python -m core.traj apply --sprite hero.png --traj traj.json --out out/punch.mp4 \
    --loops 2 --bg assets/bg_cornfield.png --sheet out/sheet.png
```

`traj.json` 里存 `summary`（帧数/尺寸/fps/检测率/左右计数）+ 每帧质心·面积·主轴角·速度 + `events`（面积局部峰，带 `strength`）。

实测（`_traj_test/fish` 51 帧 320×426 @10fps → 企鹅立绘）：检测率 98%、12 个事件、左右 26/24；`--loops 2` 出 10.92s / 262 帧 / 720²@24fps，`diff_subject 9.03 · cv 0.58 · 0 重复帧 · COHERENT`（旧一次性脚本是 263 帧 / 10.96s / 10.17，同量级）。回归：`python tests/traj_check.py`。

### 图形入口（双击即用）

```
启动NeoForgeSD.cmd   →  自动拉起 SD1.5 后端 + 控制台，并打开 http://127.0.0.1:8899
停止NeoForgeSD.cmd   →  只结束本项目的进程
```

页面四块：① 出图（txt2img）② 动作·SD 图生视频（AnimateDiff）③ 木偶动作（puppet，含连贯性自检）④ 结果播放/下载。

接口（也方便脚本调）：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/status` | 后端在线、底模、AnimateDiff、动作模板 |
| POST | `/api/image` | 出图，返回 `job_id` |
| POST | `/api/motion` | AnimateDiff 图生视频 |
| POST | `/api/puppet` | 木偶动作（`action` / `dur` / `fps` / `size` / `bg`），完成后带 `coherence` |
| GET | `/api/job/<id>` | 轮询进度与产物（`media_url`） |

### 自检（`tests/smoke.py`）

```bash
python tests/smoke.py     # 全仓 py 编译 + node --check bridge + 起控制台走完整 HTTP 链路
```

不需要 SD 后端、不需要显卡：`/api/status` → 首页 HTML → `POST /api/puppet` 出片 → 轮询 → 下载 mp4 验 `ftyp` → 未知 job 要 404 → 无后端时 `/api/motion` 要 503。约 13 秒跑完，结果写 `smoke.json`（`NEOFORGE_TEST_OUT` 可指定目录）。

---

## 兼容后端

| 后端 | 地址 | 适用模型 | 备注 |
|---|---|---|---|
| ComfyUI 0.41.0 | `:8188` | DiT 模型（Anima 等 NeoForge 加载不了的） | 需 torch ≥ 2.8 才开 DynamicVRAM |
| NeoForge WebUI（Forge Classic 兼容） | `:7860` | SD1.5 / SDXL / Illustrious | 采样器名**不带** `Karras` 后缀，调度器单独传 |

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
core/                # 双后端 HTTP 客户端 + 动作引擎(motion) + 连贯性体检(quality) + 轨迹驱动(traj)
bridge/              # MCP 桥（node，含 py/ 辅助脚本）
tests/               # 自检：smoke.py（链路冒烟）、traj_check.py（轨迹驱动回归）
tools/               # 模型下载 / checkpoint 拆分(fp8) / ComfyUI 自检等小工具
workflows/           # 现成工作流模板（SDXL 竖屏 fp8 等）
spike/               # 实测/基准脚本（选型依据留痕）
```

## License

MIT — 仅针对本仓库代码。模型权重不属于本仓库，遵循其各自许可。
