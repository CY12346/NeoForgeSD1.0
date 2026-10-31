注意：本项目是作者用于学习vibe coding的产物，作者本人的编程技术完全无法做到当前效果。

# NeoForgeSD 1.3.0

本地 Stable Diffusion **出图编排层**：双后端客户端 + 批量出图调度器 + MCP 桥。

> **v1.3.0（2026-10-31）｜壳归壳，核归核：启动器不再捆后端**
>
> 方向重定：**1.3.0 做启动器，不做整合包**。内核（生图后端）通过管道协议接进来，壳只有几 MB。
>
> | 之前（1.2.x 整合包） | 现在（1.3.0 壳 + 核） |
> |---|---|
> | 发行资产 5.55 GiB，backend 三份分卷是同一份 A1111 | 壳 11.5 MB（未压 UPX），核你自己已经有 |
> | 每发一版重传 1.8 GiB ×3 | 换核不动壳，壳可独立升级 |
> | 把用户锁死在一个后端 | 任何内核都能接（A1111 / ComfyUI / 自研） |
>
> **四件事**：环境侦探（六项状态灯 + 人话修复建议）/ 内核登记册（只认 `kernels/<名>/kernel.json`）/ 首次安装引导 / 管道（`PIPE:READY` 握手 · `/healthz` 判活 · `PIPE:PROGRESS` 进度 · 收尸不留孤儿）。
>
> **实测**：假内核就绪 0.5 s；A1111 内核就绪 4.4 s、健康检查通过、停止后 8899/7860 全释放、零孤儿；三种故障（端口占用 / models 缺失 / 显存不足）全部识别。
>
> 规范见 [`SPEC-kernel.md`](SPEC-kernel.md)，变更见 [`CHANGELOG.md`](CHANGELOG.md)。
>
> **v1.2.2（2026-10-30）｜SDXL 可用化：画质跨代 + 两处阻塞修复**
>
> 本机 4 GB 卡实测（同 prompt、同 seed 20261030、DPM++ 2M Karras）：
>
> | 档位 | 模型 | 最终分辨率 | 耗时 | 锐度（Laplacian） | 判定 |
> |---|---|---|---|---|---|
> | SD1.5 基线 | AWPainting v1.2 + 高清修复 2× | 1024×1536 | 91.3 s | 325 | 偏软 |
> | SD1.5 基线 | ReVAnimated v122 + 高清修复 2× | 1024×1536 | 90.2 s | 327 | 偏软 |
> | **SDXL** | **Illustrious-XL v0.1 直出** | **832×1216** | **440 s** | **908** | **锐利** |
>
> 锐度 **2.8 倍**；细节密度、构图完成度、光影层次全面超过 SD1.5（同一 seed 下 SD1.5 只出半身特写，SDXL 出完整场景）。
>
> **两处修复：**
> 1. **SDXL 自动配套 VAE**（`vae_for()`，`ui/image_app.py`）——以前切到 SDXL 模型时仍沿用 SD1.5 的 `vae-ft-mse`，出图整张蓝紫斑驳花屏，而 Laplacian 反而虚高到 843 骗过糊图检测。现在按模型名自动切 `madebyollin-sdxl-vae-fp16-fix`，接口还会回传实际生效的 VAE。
> 2. **后端启动加 `--skip-install`** —— A1111 每次启动重装依赖会把 `ml_dtypes` 降到 0.4.1，`transformers` 随即报 `module 'ml_dtypes' has no attribute 'float8_e3m4'` 直接起不来（本次实测复现 2 次）。现在默认跳过依赖安装；要重建依赖时设 `NEOFORGE_INSTALL_REQS=1`。
>
> **速度代价说清楚**：4 GB 卡跑 SDXL 20 步 = **7.3 分钟/张**（显存打满，每步 ~20 s）。试过 Lightning 6 步（4.5 分钟）但 blur 仅 178，属于糊图，不推荐。**结论：4 GB 上 SDXL 只用于精修单张，批量出图仍走 SD1.5。**
>
> **v1.2.0（2026-10-19）｜图形启动器：双击即用，告别黑框**
>
> 新增 **`NeoForgeSD.exe`**（12 MB，Release 附件 + 仓库 `dist/`）作为唯一入口：
> - 打开就是图形界面，自检 **SD 后端 / 基础模型 / 服务状态**，缺什么一眼看到
> - 点「启动」自动拉起后端（`--nowebui`，无 WebUI 页面）并打开浏览器
> - 用后端自带的 python 跑前端，**不依赖系统 Python 环境**
> - `停止` / `打开页面` / `重新检测` 四个按钮 + 实时日志
>
> 老的三个 `.cmd` 黑框入口已归档到 `_parked/cmd/`。
>
> **v1.1.0（2026-10-19）｜低端显卡专项：4 GB 显存跑出约 1.2 倍产能，大图从"必炸"变成"能跑"**
>
> 针对 RTX 3050 Laptop / 4 GB 这类"装得上、跑得慢、大图必 OOM"的机器，把后端调用链重调了一遍。同一张图、同一个 seed、同样 20 步：
>
> | 场景 | 改前 | 改后 | 效果 |
> |---|---|---|---|
> | 512×512 | 6.95 s | **5.73 s** | **快 17.6%** |
> | 768×768 | 18.17 s | **14.67 s** | **快 19.3%** |
> | 1024×1024 | 47.13 s | **38.60 s** | **快 18.1%** |
> | 512 + ADetailer 修脸 | 11.98 s | **9.90 s** | **快 17.4%** |
> | 2048×2048 单张 | 跑不动 | **284 s 完成** | 新能力 |
> | 768×768 × 8 连拍 | 跑不动 | **106 s 完成** | 新能力 |
>
> 做法是三条：
> 1. 后端启动参数从 `--medvram --opt-sdp-attention` 换成 `--xformers` —— 4 GB 上 medvram 的分块搬运反而拖慢 18%，而 xformers 的 memory-efficient attention 在 sm86 上比 SDPA 快 18%；
> 2. 子进程注入 `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`，治掉"显存明明够却报 OOM"的碎片问题 —— 这也是 4 GB 卡第一次能跑 2048² 和 8 连拍的原因；
> 3. 请求级 `override_settings.s_min_uncond=1`，跳过接近收敛时的负面提示词分支，纯提速、不改画面。
>
> **精度没有让步**：默认仍走 fp16 VAE，并且实测本机 `vae-ft-mse` 在 fp16 与 fp32 下输出**逐像素完全一致**（max_diff=0.0）—— 所以不需要 `--no-half-vae` 那种"用 632 MB 常驻显存换保险"的做法（实测它会把这台机器的 1024² 从 38.6 s 拖到 64 s）。同样刻意**不启用** token merging：它还能再快 20%，但会合并注意力 token 丢细节。两个开关留给极端情况：`NEOFORGE_SD_MEDVRAM=1`（显存比 4 GB 还小）、`NEOFORGE_SAFE_VAE=1`（万一遇到黑图）。
>
> **v1.0.1（2026-10-19）**：图形页面 `ui/image_app.py`（:8899）是主入口；新增**提示词助手** —— 中⇄英提示词转换（本地 8.6 万条 danbooru 中文词典，只有整句才联网兜底）、输入即补全、3958 条中文词组库、SD 风格预设一键套用。
>
> 这不是 Forge 或 ComfyUI 的分支。NeoForgeSD 是一层**独立于引擎之上**的编排代码，通过 HTTP API 驱动你已经装好的 NeoForge WebUI（Forge Classic 兼容）/ ComfyUI，把「镜头表」变成「一批落盘的图」。它**不含任何模型权重**，模型由你自己下载并遵守其各自许可。

---

## 它能干什么

一条命令，把一整批场景清单批量出图，逐张计时、记录 seed、命名落盘、失败不中断：

```bash
python manhua.py --shots shots.json --backend comfy --out out_dir --limit 10
```

三块组成：

| 模块 | 文件 | 作用 |
|---|---|---|
| 双后端客户端 | `core/comfy_client.py` `core/sdclient.py` | 封装 ComfyUI(`:8188`) 与 NeoForge WebUI(`:7860`) 的 HTTP API，把实测踩过的坑固化进去 |
| 出图调度器 | `manhua.py` | 场景清单 → 批量出图 → 命名落盘 + manifest.json |
| 图形页面 | `ui/image_app.py` `ui/image_page.html` | ①创作 ②后期 ③模型 ④资产 四组；提示词助手（中⇄英 / 词库 / 补全 / 风格）；自动以 `--nowebui` 拉起 SD 后端 |
| 提示词助手 | `core/i18n_tags.py` | 读本机 SD 扩展的 danbooru 中英词典 + 词组库；纯标准库，无 GPU 无网络也能跑 |
| MCP 桥 | `bridge/` | 把本机 AIGC 能力（SD 出图 / ComfyUI / 语音）暴露成 MCP 工具给 AI Agent |

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

工具一览：`image_generate` / `image_edit` / `image_upscale` / `image_process` / `comfy_generate` / `sd_models` / `sd_set_model` / `sd_start` / `aigc_status` / `tts_speak` / `voice_clone` / `gsv_start`（共 12 个）。

**分线开关**（出图 / 语音）：`NEOFORGE_BRIDGE_LINE=image|voice|all`，默认 `all`（向后兼容）。
`image` 只暴露 9 个图像工具、`voice` 只剩 4 个语音工具；调到线外工具会返回明确报错而不是静默失败。实测：image=9、voice=4、all=12。

---

## 图形入口（双击即用）

**`NeoForgeSD.exe` —— 唯一入口，图形界面，无命令行窗口。**

```
双击 NeoForgeSD.exe
   → 启动器自检（后端 / 模型 / 服务）
   → 点「▶ 启动」
   → 自动拉起 SD 后端并打开 http://127.0.0.1:8899/
```

启动器界面：

| 项 | 说明 |
|---|---|
| SD 后端 | 指向 `backend/`，**自带 python，不依赖系统环境** |
| 基础模型 | `models/Stable-diffusion/` 下第一个可用模型 |
| 服务状态 | 未启动 / 启动中 / 运行中（显示当前模型名） |

按钮：`启动` `停止` `打开页面` `重新检测`；日志实时写 `work/launcher.log`。

> 老的黑框 `.cmd` 已归档到 `_parked/cmd/`，不再作为入口。

小抄：页面里 `文生图 / 图生图` 两个页签；想直接落到图生图就开 `http://127.0.0.1:8899/?mode=i2i`。

**生图（`ui/image_app.py` + `ui/image_page.html`，8899，独立前端）** —— 把 SD 里能通过 API 做的功能全搬过来，按 **4 组** 摆：

| 组 | 里面有什么 | 对应 SD |
|---|---|---|
| **① 创作** | 文生图 / 图生图 / **局部重绘（蒙版笔刷）**；底部「高级」= ADetailer 面部修复 · LoRA · 高清修复；右侧画布 | txt2img / img2img / inpaint / ADetailer / LoRA / Hires.fix |
| **② 后期** | 放大（2/3/4×、4x-AnimeSharp…）+ **GFPGAN / CodeFormer 修脸** + 选型经验 | Extras |
| **③ 模型** | 底模热切换 · **VAE 切换** · CLIP skip · 采样器/调度器 | Settings / checkpoint 选择 |
| **④ 资产** | 历史（点图回填） · **PNG 信息（读参数并回填表单）** · 打开产物目录 | PNG Info |

- 尺寸预设带实测耗时（512²≈6s · 768²≈19s · 768×1344≈62s）；1/2/4/8 张串行不涨显存
- 局部重绘：画布上直接涂蒙版（笔刷大小/羽化/遮罩底可调），只重绘涂过的地方（实测蒙版外像素差 0.07，蒙版内 42.5）
- 全量功能对照与「不做什么」见 `FEATURES-SD.md`

**后端由谁管**：页面打开时若检测到后端没跑，会自动以 `--nowebui` 拉起（60–120 秒，顶栏显示「后端启动中 xx s」）；
「停止后端」可释放显存。`--nowebui` 只是不生成 WebUI 页面，**扩展脚本照常加载**（实测 `/sdapi/v1/scripts` 里 `adetailer` / `controlnet` 都在，ADetailer 走 `alwayson_scripts` 正常生效）。

**默认参数 = 老 SD WebUI 的基线**（对着 `ui-config.json` / `config.json` 核过）：`512×512` · 20 步 · `DPM++ 2M` + `karras` · CFG 7 · CLIP skip 2 · VAE `vae-ft-mse-840000-ema-pruned` · Hires off。
SD1.5 在 768² 以上容易被拉长/崩肢体 —— 想出大图请用「放大」或「高清修复」，别直接拉分辨率。

接口（脚本也能调）：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/status` | 在线、当前底模、模型/LoRA/采样器/调度器/放大模型清单、后端启动状态 |
| POST | `/api/image` | 文生图（`n` 张串行、`loras[]`、`hires{}`） |
| POST | `/api/img2img` | 重绘（`image_b64` + `denoise`） |
| POST | `/api/upscale` | 放大（`upscaler` + `scale`） |
| POST | `/api/model` | 切换底模 |
| POST | `/api/vae` | 切换 VAE（本机 6 个） |
| POST | `/api/pnginfo` | 从图读参数（PNG Info） |
| POST | `/api/options` | 全局选项（目前是 `clip_skip`） |
| POST | `/api/start_sd` / `/api/stop_sd` | 拉起 / 停止无界面后端 |
| POST | `/api/open_folder` | 打开产物目录 |
| GET | `/api/history?limit=30` | 出图历史（含参数，可回填） |
| GET | `/api/job/<id>` | 轮询进度与产物（`image_urls`） |
| GET | `/files/<name>` | 产物文件 |


### 自检（`tests/smoke.py`）

```bash
python tests/smoke.py     # 全仓 py 编译 + node --check bridge + 起控制台走完整 HTTP 链路
```

不需要 SD 后端、不需要显卡：`/api/status`（离线但字段完整）→ 首页三区块（文生图 / 重绘 / 放大 + LoRA/高清修复）→ `/api/history` → 无后端时 `/api/image`、`/api/img2img`、`/api/upscale`、`/api/model` 都要 503 → 未知 job 要 404。约 15 秒跑完，结果写 `smoke.json`（`NEOFORGE_TEST_OUT` 可指定目录）。

> 冒烟会把自己的 `NEOFORGE_SD_API` 指向关闭端口（默认 `http://127.0.0.1:9`），所以**本机 SD 正在跑也能跑冒烟**；要连真后端测，用 `NEOFORGE_TEST_SD_API=http://127.0.0.1:7860`。

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
manhua.py            # 出图调度器（图像主线 CLI）
ui/image_app.py      # 生图前端（8899；SD 后端由它无界面拉起）
core/image/          # comfy_client + sdclient —— 出图主线（ComfyUI / NeoForge WebUI 客户端）
core/*.py            # 同名兼容 shim：旧 import 照旧可用
bridge/              # MCP 桥（node，含 py/ 辅助脚本；支持 NEOFORGE_BRIDGE_LINE 分线）
tests/               # 自检：smoke.py（生图线端到端冒烟）
tools/               # 模型下载 / checkpoint 拆分(fp8) / ComfyUI 自检等小工具
workflows/           # 现成工作流模板（SDXL 竖屏 fp8 等）
spike/               # 实测/基准脚本（选型依据留痕）
```

## License

MIT — 仅针对本仓库代码。模型权重不属于本仓库，遵循其各自许可。
