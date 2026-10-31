# 更新日志

## v1.3.0（2026-10-31）｜壳归壳，核归核：启动器不再捆后端

**方向重定**：1.3.0 不再做整合包，改做启动器。内核（生图后端）通过管道协议接进来，壳只有几 MB。

### 新增

- **`kernels/` 内核登记册**：加一个内核 = 加一个目录 + 一份 `kernel.json`，**启动器代码零改动**。
  - `kernels/fake`：10 行假内核，管道自测用，不出图
  - `kernels/a1111`：把现成的 NeoForgeSD 前端当内核登记进去
- **管道协议**（`core/pipe.py`）：spawn → `PIPE:READY` 握手 / 健康轮询 → `PIPE:PROGRESS` 进度 → 收尸（温和终止 → 整棵进程树强杀 → 验证端口释放、无孤儿）
- **环境侦探**（`core/detect.py`）：后端 Python / 基础模型 / 端口 / 磁盘 / 显卡显存 / 内核登记册六项状态灯，每颗红黄灯配一句人话 + 一个修复动作
- **`SPEC-kernel.md`**：`kernel.json` 规范与管道协议（spec v1，兼容 N-1）
- **自测**：`tests/test_pipe.py`（起·判活·进度·收尸·三种故障识别）、`tests/test_launcher_smoke.py`（壳真建窗口）、`tests/test_kernel_a1111.py`（真起真停 A1111）

### 实测

- 假内核：就绪 **0.5 s**，3 条进度事件，收尸后进程树全清、端口释放
- A1111 内核：就绪 **4.4 s**，`/api/status` 健康通过，停止后 8899/7860 全释放、零孤儿
- 三种故障（端口占用 / models 缺失 / 显存不足）全部识别并给出可执行建议
- 壳打包：PyInstaller onefile → **11.46 MB**（12,014,016 B，未压 UPX）；双击起窗标题 `NeoForgeSD 1.3.0`，
  任务管理器可见 bootloader + 子进程两个，`taskkill /T /F` 后零残留
  - 计划里的目标是 <10 MB，**未达成**：UPX 未安装（PyPI 无对应包）。装上 UPX 后重新打包应可压到 10 MB 以下

### 修复

- 启动器 `WORK` 未定义（1.2.2 遗留）：模型联接成功后打日志会 `NameError`
- 壳不再假定后端布局：`{base}` 占位符 + 脚本查找路径，同一份 `kernel.json` 在便携包与开发仓库两种布局下都能用

### 变更

- `dist/NeoForgeSD.exe` 移出仓库（Release 附件照发），仓库不再跟踪二进制
- 新增 CI 工作流（`.github/workflows/ci.yml`：Linux 全量 `py_compile` + 管道自测 + 仓库卫生）
  —— **待授权入库**：推 `.github/workflows/*` 需要 token 带 `workflow` 权限，
  当前 token 只有 `gist/read:org/repo`；文件已在本机就绪，授权后补推即可
- 1.2.x 的 5.55 GiB 发行资产转为 **legacy**：保留可下，不再引导新用户

## v1.2.2（2026-10-30）｜SDXL 可用化

- SDXL 自动配套 VAE（`vae_for()`），不再沿用 SD1.5 的 `vae-ft-mse`
- 后端启动加 `--skip-install`，避免每次重装依赖把 `ml_dtypes` 降级导致起不来
- 4 GB 卡实测：SDXL 832×1216 = 440 s / 锐度 908（SD1.5 高清修复 91 s / 325）

## v1.2.1（2026-10-17）｜可分发包

- 便携路径推导 + 自动 `models` 目录联接 + 安装说明

## v1.2.0（2026-10-19）｜图形启动器

- `NeoForgeSD.exe`：双击即用，自检 + 起停 + 日志；旧 `.cmd` 入口归档 `_parked/cmd/`

## v1.1.0（2026-10-19）｜4 GB 卡专项

- 低端显卡调用链重调，约 1.2 倍产能，大图从"必炸"变成"能跑"

## v1.0.1 / v1.0.0

- 首个可用版本：双后端客户端 + 批量调度 + MCP 桥
