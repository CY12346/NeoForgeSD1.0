# 内核文档 · neoforge-sd15（NeoForgeSD 的 SD1.5 内核）

> 版本 1.3.0 · 契约 `kernel.json` spec v1 · 2026-11-03 拆分
> 一句话：**这是「核」**——自带前端页面 + 会拉起 SD WebUI 后端；壳只按 `kernel.json` 起停它，不认识里面一行代码。

---

## 1. 边界：核管什么，壳管什么

| | 核（本目录） | 壳（`NeoForgeSD.exe`） |
|---|---|---|
| 代码 | `app/ui/image_app.py`（730 行）、`image_page.html`、`app/core/{winproc,i18n_tags}.py` | 启动器界面、内核登记册、管道、维修、打包、下载 |
| 干的活 | 出图前端（8899）+ 拉起 SD 后端（7860）+ 提示词中⇄英 + 历史/放大/重绘 | 起核、判活、收进度、收尸、亮状态灯、装包/下载 |
| 不知道什么 | 不懂启动器、不读 `work/setup.json` | 不认识 A1111、不认识 ComfyUI、不碰 `webui.py` |
| 重量 | **包 8 个文件 105 KiB（zip 37 KiB）** | exe 12.5 MB |

**关键设计**：核不含后端与权重。`backend/`（3 GB 级，带 python+torch）与 `models/`（225 GB）
通过环境变量指给它——同一份核可以指向任意一份 A1111/Forge 目录。

---

## 2. 目录

```
kernels/neoforge-sd15/
├── kernel.json          ← 契约（手写）。壳唯一读的东西
├── kernel_main.py       ← 入口（手写）。环境自检 → PIPE 行 → 起 server
├── README.md            ← 短自述
├── manifest.json        ← 构建产物：16 个文件的 size + sha256
└── neoforge/            ← 内核自己的包（手写，8 层，见 KERNEL-ARCH.md）
    ├── config.py            路径 / 端口 / 开关 / 基准目录推导
    ├── sdapi.py             SD HTTP 客户端 + 目录缓存（唯一跟 7860 说话的地方）
    ├── engine.py            后端进程起停 / 判活 / 日志 / 启动参数
    ├── payload.py           页面参数 → SD 载荷（纯函数）
    ├── jobs.py              任务注册表 + 工作线程 + 落盘
    ├── history.py           历史（work/history_image.json，原子写）
    ├── textfmt.py           中⇄英 / 补全 / 词库（包装 _vendor/i18n_tags）
    ├── server.py            HTTP 路由（唯一对外的层）
    ├── ui/image_page.html   ← 构建复制（源在仓库 ui/）
    └── _vendor/             ← 构建复制（源在仓库 core/）：winproc / i18n_tags
```

分层与依赖规则见 **`KERNEL-ARCH.md`**。源只有一份：仓库 `ui/`、`core/` 里那三件是复制品，
其余内核代码就在本目录手写维护。改完复制品的源要跑 `python tools/build_kernel.py` 重新 stage。

---

## 3. 依赖（它不带来，你得给它）

| 依赖 | 要什么 | 缺了会怎样 |
|---|---|---|
| SD 后端目录 | 里面必须有 `python\python.exe` 和 `webui.py`（A1111 / Forge 官方整合包都有） | 退出码 **3** + 人话「SD 后端里没有 python」 |
| 权重 | `models/Stable-diffusion/` 至少一个底模；SDXL 系模型要配 SDXL VAE | 前端能开，/api/status 里 `model=null`，出图 500 |
| 提示词词典（可选） | `backend/extensions/a1111-sd-webui-tagcomplete/tags/*.csv`、`sd-webui-prompt-all-in-one/group_tags/zh_CN.yaml` | 中⇄英翻译退化为在线兜底；不影响出图 |
| GPU | 4 GB 显存够 512²/1024²；SDXL 832×1216 也跑得下（7.3 分钟/张） | 显存不足报 OOM，日志在 `work/sd_server.log` |

---

## 4. 三种起法

**① 壳里起（正常用法）**
启动器 → 左栏内核选 `neoforge-sd15` → 启动。壳按 `kernel.json` 的 `command`/`env`/`port` 办事。

**② 命令行直接起（不用壳）**
```bash
E:/NeoForge/backend/python/python.exe E:/NeoForge/app/kernels/neoforge-sd15/kernel_main.py
# 不给环境变量时，它会自己往上找 backend/python/python.exe（最多三层）
```

**③ 塞进别的壳**
把 `dist/kernels/neoforge-sd15-1.3.0.zip` 解开，整个 `neoforge-sd15/` 目录丢进那个壳的 `kernels/`。
壳只要认 spec v1，就能起它——**不需要改壳一行代码**。

---

## 5. 环境变量（壳按 `kernel.json` 塞进来）

| 变量 | 默认 | 作用 |
|---|---|---|
| `NEOFORGE_SD_HOME` | 往上找三层里的 `backend` | SD 后端目录。**这是内核唯一必须外部给的路径** |
| `NEOFORGE_IMAGE_PORT` | 8899 | 前端端口 |
| `NEOFORGE_IMAGE_WORK` | `{SD_HOME 上一级}/work` | 历史、日志、产物索引 |
| `NEOFORGE_SD_API` | `http://127.0.0.1:7860` | SD 后端 API（后端已在别处跑时指过去即可复用） |
| `NEOFORGE_SD_MEDVRAM` | 关 | `=1` 给小于 4 GB 的卡加 `--medvram`（4 GB 卡**不要**开，1024² 慢 18%） |
| `NEOFORGE_SAFE_VAE` | 关 | `=1` 加 `--no-half-vae`（黑图/色偏时才开，显存 +632 MB） |
| `NEOFORGE_SDXL_VAE` | `madebyollin-sdxl-vae-fp16-fix.safetensors` | 切到 SDXL 系模型时自动配的 VAE |

后端启动参数（内核自己决定，写死在 `image_app.py`，不在壳里）：
`--api --nowebui --port 7860 --xformers --skip-version-check --skip-install`

---

## 6. 管道协议（stdout 逐行）

| 行 | 何时 | 实测输出 |
|---|---|---|
| `PIPE:PROGRESS 0.10 检查环境` | 启动即打 | `0.1 检查环境` → `0.35 环境就绪（后端 python 已确认）` → `0.70 载入内核代码完成，开始绑定端口` |
| `PIPE:READY port=8899` | **端口真正绑上之后**（不是进程一起来就报） | `收到握手：PIPE:READY port=8899` |
| `PIPE:LOG …` | 普通日志 | 原样进壳的日志窗 |

`ready.mode = "both"`：握手行与 `/api/status` 轮询谁先到算谁。健康检查 `/api/status`，
2 s 一次、3 s 超时、连续 3 次失败转黄（不立刻杀）。

---

## 7. HTTP 接口（前端 8899，`127.0.0.1` only）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/status` | 状态灯/模型/后端启动进度。**健康检查就打它** |
| GET | `/api/catalog` | 底模、采样器、调度器、LoRA、VAE 清单（缓存 30 s） |
| POST | `/api/start_sd` · `/api/stop_sd` | 拉起/停掉 7860 上的 SD 后端 |
| POST | `/api/image` | 文生图 → `{job_id}`（异步，512²·8 步约 7 s） |
| POST | `/api/img2img` · `/api/upscale` | 重绘 / 放大（同样返回 job_id） |
| GET | `/api/job/<id>` | 任务状态：`queued/running/done/error` + `files` + `elapsed` |
| GET | `/api/progress` | 透传 SD 的采样进度 |
| GET | `/api/history` · `/api/suggest` · `/api/promptlib` · `/api/i18n` · `/api/translate` | 历史、补全、词库、中⇄英 |
| POST | `/api/model` · `/api/vae` · `/api/options` | 切底模（SDXL 自动配 VAE）/ 切 VAE / 改设置 |
| GET | `/files/<名字>` | 取 `work/` 下的产物 |

---

## 8. 故障表（都真遇到过）

| 现象 | 原因 | 怎么办 |
|---|---|---|
| 退出码 3「找不到 SD 后端目录」 | `NEOFORGE_SD_HOME` 没给或指错 | 指向带 `python/python.exe` 的目录；壳里检查 `{base}/backend` 是否存在 |
| 退出码 3「没有 python」 | 只下了 A1111 源码，没有官方整合包 | 下官方整合包（自带 python+torch），源码跑不起来 |
| 退出码 2「端口 8899 绑不上」 | 上一次的内核没退干净 | 壳里先停止；`netstat -ano | grep 8899` 找 pid，用 `Stop-Process -Id N` |
| `/api/image` 返回 503「SD 后端未启动」 | 只起了前端 | 点「启动后端」，等 `/api/status` 里 `online=true`（首启 143 s） |
| 出图整张蓝紫花屏 | SDXL 模型沿用了 SD1.5 的 VAE | 用 `/api/model` 切模型（会自动配 `sdxl-vae-fp16-fix`） |
| 4 GB 卡 1024² OOM | 开了 `--medvram` 或 `--no-half-vae` | 关掉这两个（默认就是关的）；`PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` 已内置 |
| 壳里状态灯绿但页面打不开 | 浏览器走了别的主机名 | 只监听 `127.0.0.1`，用 `http://127.0.0.1:8899/` |

---

## 9. 构建与分发

```bash
cd E:/NeoForge/app
python tools/build_kernel.py            # stage + manifest.json + dist/kernels/neoforge-sd15-1.3.0.zip
python tools/build_kernel.py --verify   # 回读：多一个文件、少一个文件、改一个字节都会报
```

分发物：`dist/kernels/neoforge-sd15-1.3.0.zip`
**43.38 KiB · sha256 `f57393a4dbe595ea6ac64ab69bc2100279914295c34a578493811be646410eec`**（源码基点 `5ccde89`）
包内 16 个文件 / 109.67 KiB：8 层 Python + 入口 + 清单 + 页面 + 两个 vendor 小件。

`manifest.json` 里每个文件都有 size + sha256，收包的人可以自己核；`--verify` 连「目录里多出来的文件」也报。

---

## 10. 实测记录（2026-11-03，RTX 3050 Laptop 4 GB）

| 项 | 实测 |
|---|---|
| 内核拉起（壳管道 `KernelRunner.start`） | **4.6 s** 到绿（含握手 + 健康检查） |
| 握手 | `PIPE:READY port=8899` · 进度 0.10/0.35/0.70 三段都到位 |
| `/api/status` | 200，`sd_home` 回报的就是壳给的那份 `E:\NeoForge\backend` |
| 收尸 | 停后端口释放、`kernel_main.py`/`webui.py` 残留进程 **0** |
| 缺环境 | `NEOFORGE_SD_HOME` 指向不存在目录 → 退出码 **3** + 中文原因 |
| 构建 | 8 个文件 / 105.08 KiB / zip 37.21 KiB；`--verify` 8 个文件 sha256 全对 |
| 收尸回归 | `tests/test_pipe_killtree.py`：桩自 spawn 子进程 → stop 后子进程确实死了，`clean=True` |
| 端到端出图 | 见 §11 |

回归脚本：`tests/test_kernel_sd15.py`（18 项断言：清单/起停/判活/收尸/缺环境，15 s）、
`tools/e2e_kernel_sd15.py`（真起后端 + 真出图 + 收干净，约 4 分钟）。

---

## 11. 端到端出图（同一份拆出来的内核）

流程：壳的管道起核 → 核自己拉起 SD 后端 → 真出一张 512² → 连后端一起收掉。

| 步骤 | 实测 |
|---|---|
| 内核就绪 | **2.7 s**（`PIPE:READY port=8899` + `/api/status` 通过） |
| SD 后端上线 | **30.4 s**（模型已缓存；冷启 161.9 s）· 模型 `SD1.5动漫\AWPainting_v1.2.safetensors [3d1b3c42ec]` |
| 出图 | **5.2 s** · `work/5486b6d268ca_00.png` · 378,349 B · **512×512** · 画面正常（人物/盔甲/夕阳，无花屏色偏） |
| 收尸 | 前端端口释放 ✅ · `pipe` 判定**干净** · stop 前抓到的 3 个后代（SD 后端 python + 2 个 conhost）**全死** |

> 收尸这里修掉一个真 bug：旧 `_kill_tree_psutil()` 到后来才去 `psutil.Process(self.proc.pid).children()`，
> 而父进程在 Windows 上 terminate 后立刻消失 → 抛异常 → 空列表 → **SD 后端活着占着 4 GB 显存**
> （10 分钟内实测漏了两次，`netstat :7860` 一直 LISTENING）。现在改成用 `stop()` 开头就抓好的 pids 去杀，
> 回归脚本 `tests/test_pipe_killtree.py` 用一个「自己 spawn 子进程」的桩守住。

---

## 12. 与老内核的关系

| 内核 | 命令 | 状态 |
|---|---|---|
| `kernels/neoforge-sd15`（本文件） | `{kernel}/kernel_main.py` | **推荐**：代码在核里，可搬可分发，环境靠 env 声明 |
| `kernels/a1111`（仓库内联） | `{base}/app/ui/image_app.py` | 留着当对照：代码在壳的仓库里，核目录只有一个 json |
| `kernels/fake` | 假内核 | 管道自测用，不出图 |

两者跑的是同一份源码；差别只在「代码归谁」——这也正是这次拆分要解决的问题。
**两者端口都是 8899，只能二选一**（管道在起第二份时直接拒：端口已被占用）。
