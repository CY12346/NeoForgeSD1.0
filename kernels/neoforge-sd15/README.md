# neoforge-sd15 · NeoForgeSD 的 SD1.5 内核

这个目录是**核**，不是壳。壳（`NeoForgeSD.exe`）只读这里的 `kernel.json`，按里面的
`command / port / health` 起停，别的什么都不管。

完整说明见仓库根 `KERNEL-neoforge-sd15.md`；**内部分层与依赖规则见 `KERNEL-ARCH.md`**。

## 目录

| 文件 | 谁维护 | 说明 |
|---|---|---|
| `kernel.json` | 手写 | 契约。壳唯一读的东西：命令、端口、健康检查、出图目录、需要什么 |
| `kernel_main.py` | 手写 | 入口。查环境 → 打 `PIPE:` 行 → 起 `neoforge.server` |
| `neoforge/` | 手写（8 层） | `config → sdapi → {engine, payload, jobs, history, textfmt} → server` |
| `neoforge/ui/image_page.html` | **构建复制** | 源在仓库 `ui/image_page.html` |
| `neoforge/_vendor/{winproc,i18n_tags}.py` | **构建复制** | 源在仓库 `core/` |
| `manifest.json` | 构建产物 | 每个文件的 size + sha256，用于分发校验 |

## 起它

有壳：启动器里选中 `neoforge-sd15` → 启动。
没壳：`<带 python 的 python> kernel_main.py`，它会自己往上找 `backend/python/python.exe`。

它要的环境（壳会按 `kernel.json` 的 `env` 塞进来）：

```
NEOFORGE_SD_HOME    A1111/Forge 目录（里面必须有 python/python.exe 和 webui.py）
NEOFORGE_IMAGE_PORT 前端端口，默认 8899
NEOFORGE_IMAGE_WORK 历史/日志目录，默认 {基准目录}/work
NEOFORGE_SD_API     SD 后端 API，默认 http://127.0.0.1:7860
```

## 改了代码之后

```bash
cd E:/NeoForge/app
python tools/build_kernel.py            # 重新 stage + 出 dist/kernels/neoforge-sd15-<ver>.zip
python tools/build_kernel.py --verify   # 回读校验 sha256（多一个少一个都会报）
NEOFORGE_SILENT=1 python tests/test_kernel_sd15.py   # 32 项断言：清单/分层/接口面/起停/收尸/缺环境
```
