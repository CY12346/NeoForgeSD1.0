# aigc-bridge — 本机 AIGC 软件 → Hermes MCP 工具桥

把这台机器上**本地安装**的图像 / 视频 / 语音软件，统一包装成 MCP 工具，挂进 Hermes。
官方 MCP 目录里没有任何"本机软件桥"，所以这套是自建的。

## 一、接入了什么

| 后端 | 来源 | 工具 |
|---|---|---|
| **Stable Diffusion WebUI**（秋叶 v4.8） | `E:\sd\sd-webui-aki-v4.8`，HTTP API `127.0.0.1:7860` | `sd_start` `sd_models` `sd_set_model` `image_generate` `image_edit` `image_upscale` `video_generate` |
| **AnimateDiff**（SD 扩展） | `extensions\sd-webui-animatediff`，动作模块 `mm_sd15_v3` | 由 `video_generate` 驱动（文生视频 / 图生视频） |
| **ffmpeg / ffprobe 7.1.1** | `E:\LosslessCut\resources\` | `video_probe` `video_ffmpeg` |
| **Blender 5.2** | `D:\Blender\blender.exe` | `blender_run` |
| **Pillow 12.3**（Python 3.12） | `E:\python\python.exe` | `image_process`（不占显存，纯 CPU 批处理） |
| **edge-tts 7.3.1** | 同上 Python | `tts_speak` |
| **GPT-SoVITS API** | GSV-TTS-Lite，`D:\aigc\GSV-TTS-Lite\API`，端口 9880 | `voice_clone` `gsv_start` |
| 全栈体检 | — | `aigc_status` |

**共 15 个工具。**

### GSV-TTS-Lite（语音克隆后端）

真正的 GPT-SoVITS 推理引擎，作者就是为 RTX 3050 Laptop 写的（比原版快 3~4 倍、显存减半）。

- 环境：`D:\aigc\gsv-env`（Python 3.12 + torch 2.11.0+cu128）
- API：`D:\aigc\GSV-TTS-Lite\API\personal_api.py`，`POST /tts` 兼容官方 api_v2 协议
- 用 `gsv_start` 启动；启动脚本 `D:\aigc\启动GSV-语音克隆.cmd`
- 首次运行自动下模型：按延迟自动选 **ModelScope（国内快）** 或 HuggingFace
- `ref_audio_path` 同时作为音色与风格参考；`prompt_text` 是它的原文（留空则需 qwen-asr 自动识别）


## 二、工具速查

### 图像
- `image_generate` — 文生图。支持 `<lora:名字:0.8>` 语法、`model` 参数直接切大模型、`batch_size`/`n_iter` 批量
- `image_edit` — 图生图，`denoising_strength` 控制漂移幅度（0.3 微调 / 0.5 换风格 / 0.8 重画）
- `image_upscale` — R-ESRGAN 等放大，适合成品封面收尾
- `image_process` — **离线批处理**，13 种操作：
  - `cover`（按 16:9 / 9:16 / 1:1 裁封面，最常用）
  - `watermark`（文字/图片水印，自动找中文字体）
  - `resize` `crop` `thumbnail` `convert` `montage`（九宫格联系表）`rotate` `flip` `border` `info`
  - 支持 `glob` 通配批量

### 视频生成
- `video_generate` — 文生视频 / 图生视频（给 `init_image` 即图生视频）
  - `video_length` 帧数、`fps`、`motion_model`、`formats: ["MP4","GIF"]`
  - 返回 MP4/GIF 绝对路径

### 视频处理（全离线，最稳）
- `video_probe` — 时长/分辨率/帧率/编码/码率
- `video_ffmpeg` — 16 种命名操作：
  `cut` `concat` `convert` `compress` `gif` `frames` `audio` `mute` `replace_audio`
  `speed` `resize` `thumbnail` `normalize`（响度标准化）`watermark` `subtitles`（烧字幕）`custom`

### 3D / 语音
- `blender_run` — 后台跑 Blender Python 脚本（渲染序列、程序化资产）
- `tts_speak` — edge-tts 配音（默认 `zh-CN-XiaoxiaoNeural`，可调语速/音调）
- `voice_clone` — GPT-SoVITS 声音克隆（参考音频 + 对应文本）

## 三、注册状态

已写入 `config.yaml`：

```yaml
mcp_servers:
  aigc:
    command: C:\Program Files\nodejs\node.exe
    args:
      - E:\hermes agen\...\profiles\sandbox\mcp\aigc-bridge\server.mjs
    enabled: true
    timeout: 3600          # 出视频动辄几分钟，默认 120s 会截断
```

**改动后需新开会话**（`Start a new session to use these tools`）。

## 四、本机踩过的坑（都已修，勿回退）

1. **ml_dtypes 损坏导致 SD 起不来**
   `module 'ml_dtypes' has no attribute 'float8_e3m4'`。
   扩展的 install requirements 会把 `ml_dtypes` 降到 0.4.x，而 jax 0.6.2 需要 0.5.x。
   修：`pip install "numpy==1.26.2" "ml_dtypes==0.5.3"`（等价于 `E:\sd\修复依赖-ml_dtypes.bat`）。
   → 所以 `sd_start` 固定带 `--skip-install`，避免每次启动又被降级。

2. **注意力优化退化**（**2026-10-16 已修复**）
   没装 xformers 时 `Automatic` 会退到 **Doggettx**，AnimateDiff 实测 **143 秒/步**。
   当时修法是加 `--opt-sdp-attention`，但 **NeoForge（Forge Classic）不认这个参数**，`launch.py` 会
   直接报 `unrecognized arguments` 退出 —— `sd_start` 因此一直起不来。
   现已装好 `xformers 0.0.25.post1+cu118`（启动日志自证），自动生效，故该参数已删除。
   → `sd_start` 固定 `--api --skip-install`。
   → 若将来又出现"每步上百秒"，先看启动日志里有没有 `xformers version`。

3. **undici 5 分钟 headers 超时**
   全局 `fetch` 有无法用 AbortSignal 延长的 5 分钟硬超时，长生成必挂。
   修：改用 `node:http` 实现 `httpJson`。

4. **keep-alive 竞争导致假 `socket hang up`**
   uvicorn 空闲超时与连接池复用竞争，图**已经生成完**却报网络错。
   修：本地调用一律 `agent: false` + `Connection: close`。

5. **大文件内联 base64 打爆传输**
   51 MB 的 GIF 内联进 JSON-RPC 直接断连。
   修：图片内联上限 3 MB，超出只回路径；gif 默认限 10 秒。

6. **AnimateDiff 参数名**
   是 `interp` 不是 `interpolate`；API 模式用 dict kwargs，视频会 base64 直接返回（按魔数识别格式，不靠顺序）。

## 五、环境变量（都可覆盖）

`SD_WEBUI_URL` `SD_ROOT` `SD_PYTHON` `SD_ARGS` `SD_LAUNCHER`
`FFMPEG_BIN` `FFPROBE_BIN` `BLENDER_BIN` `PYTHON_BIN` `GSV_URL` `AIGC_OUT_DIR`

## 六、自测

```bash
cd <this dir>
node test_client.mjs status imgproc video tts blender   # 不需要 SD
node test_client.mjs sd vidgen edit                     # 需要 SD 在跑
```

产物默认落在 `<profile>\outputs\aigc\<时间戳>_<tag>\`。
