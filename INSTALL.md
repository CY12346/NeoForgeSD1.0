# NeoForgeSD 1.2.1 可分发包安装说明

从 Release 下载四件套，解压即用：**不需要装 Python、不需要 pip、不需要 CUDA 工具链**。

## 一、下载

| 顺序 | 文件 | 说明 |
|---|---|---|
| 1 | `NeoForgeSD.exe` | 图形启动器 |
| 2 | `NeoForgeSD-app-1.2.1.zip` | 前端与业务代码 → 解出 `app/` |
| 3 | `NeoForge-backend-1.2.1.7z.001` … `.00N` | 后端运行体分卷，**必须全部下完** |
| 4 | 底模 `.safetensors` | 如 Release v1.2.0 的 `AWPainting_v1.2.safetensors` |

## 二、目录结构（解压后必须长这样）

```
NeoForgeSD\
  NeoForgeSD.exe
  app\          ← 第 2 项解压得到
  backend\      ← 第 3 项从 .7z.001 解压得到（自带 python）
  models\       ← 分卷里已带 VAE / CodeFormer / GFPGAN / ESRGAN / RealESRGAN
    Stable-diffusion\   ← 底模放这里
  work\         ← 首次启动自动生成
```

## 三、步骤

1. 分卷放同一文件夹 → 右键 `.7z.001` → 7-Zip → 解压到当前文件夹 → 得到 `backend/`
2. `app/`、`NeoForgeSD.exe`、`backend/`、`models/` 放在同一层
3. 底模放进 `models\Stable-diffusion\`
4. 双击 `NeoForgeSD.exe` → 「启动」→ 浏览器打开 http://127.0.0.1:8899/

启动器首次运行会自动建立 `backend\models` → `models` 的目录联接（A1111 只认 `backend/models`）。
若被拦下，手动执行：`mklink /J backend\models models`

## 四、包里有什么

- Python 3.10.11 + torch 2.1 (CUDA 11.8) + 稳定扩散 WebUI 1.10.1，以 `--nowebui` 只跑 API 后端
- 扩展：ADetailer、a1111-sd-webui-tagcomplete（8.6 万中文 tag）、prompt-all-in-one、简体中文语言包
- 离线权重：CodeFormer、GFPGAN、vae-ft-mse-840000、`4x-AnimeSharp` / `RealESRGAN_x4plus`、
  HuggingFace 缓存（ADetailer 人脸检测）
- 为缩小体积删除了 `*.lib`（C++ 链接期文件，运行时从不加载，省 742 MB）

## 五、常见问题

- **「找不到 SD 后端」**：`backend/` 没和解压位置对齐，或分卷没下全。
- **「未找到 .safetensors」**：`models\Stable-diffusion\` 是空的，第 3 步没做。
- **首次启动慢**：加载模型 60–120 秒属正常，日志在 `work\sd_server.log`。
- **显存**：4 GB 可跑 512²（约 6 秒）与 1024²（约 38 秒）；不要额外加 `--no-half-vae`（会拖慢一倍）。
