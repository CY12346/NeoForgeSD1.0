# NeoForgeSD 1.3.0 安装说明

1.3.0 起**壳不含后端**：启动器只有几 MB，内核（生图后端）单独装、随时换。

## 一、三样东西

| 东西 | 是什么 | 多大 |
|---|---|---|
| `NeoForgeSD.exe` | 壳：状态灯、内核列表、起停、日志 | 11.5 MB |
| 内核目录（如 `backend/`） | 真正出图的后端，自带 python | 1–2 GB |
| `models/` | 底模与放大/修脸权重 | 按需 |

## 二、目录结构

```
NeoForgeSD\
  NeoForgeSD.exe
  kernels\           ← 内核登记册，每个子目录一份 kernel.json
    a1111\
      kernel.json
    fake\
      kernel.json
      fake_kernel.py
  backend\           ← 内核一：A1111 运行体（自带 python）
  app\               ← 前端与业务代码（被 a1111 内核使用）
  models\
    Stable-diffusion\   ← 底模放这里
  work\              ← 运行产物与日志
```

## 三、跑起来

1. 双击 `NeoForgeSD.exe` → 壳自己跑环境侦探，六个状态灯直接告诉你「缺什么、怎么补」
2. 下拉框选内核（`a1111` 或 `fake`），点「启动」
3. 就绪后自动打开 http://127.0.0.1:8899/

**第一次只想验证壳能不能用**：选 `fake` 内核 —— 它不出图，只证明管道（起 · 判活 · 进度 · 收尸）是通的。

## 四、加一个新内核

1. 建目录 `kernels\<名字>\`
2. 写 `kernel.json`（字段见 [`SPEC-kernel.md`](SPEC-kernel.md)），至少给 `spec / id / name / command / port`
3. 重启壳 → 下拉框里就多一项

内核要配合管道，只需在 stdout 上打一行 `PIPE:READY port=<端口>`，并提供 manifest 里声明的健康检查路径。
不想改内核也行：`ready.mode` 写 `poll`，壳直接轮询健康检查。

## 五、常见问题

- **状态灯：后端 Python 红** —— `backend/` 没和 exe 放同一层；A1111 内核需要它自带的 python。
- **状态灯：基础模型红** —— `models\Stable-diffusion\` 是空的。
- **状态灯：端口黄** —— 8899 被占用：要么它就是在跑（直接开页面），要么改该内核 `kernel.json` 的 `port`。
- **「起不来」** —— 日志窗最后一屏会写具体原因（命令不存在 / 工作目录不对 / 等就绪超时）。
- **停止后要干净** —— 壳会先温和终止、再强杀整棵进程树，最后验证端口释放；不留孤儿是硬要求。
- **显存** —— 4 GB 可跑 512²（约 6 秒）与 1024²（约 38 秒）；不要加 `--no-half-vae`（会拖慢一倍）。

## 六、从 1.2.x 升上来

1.2.x 是整合包（5.55 GiB，含 backend 与 models）。升级时**不用重下**：
把旧的 `backend/`、`models/`、`app/` 挪到新 `NeoForgeSD.exe` 同级，再放上 `kernels/` 即可。
旧 Release 仍可下载，但已标 legacy，不再引导新用户。
