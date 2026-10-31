# 内核契约 · kernel.json spec v1

> NeoForgeSD 1.3.0 起，**壳归壳，核归核**。启动器不认识任何内核代码，只认 `kernels/<名字>/kernel.json`。
> 加一个内核 = 加一个目录 + 写一份 json，**不改启动器一行代码**。

## 0. 术语

| 词 | 意思 |
|---|---|
| 壳（launcher） | 状态灯、内核列表、起停、日志、进度条。不含任何出图后端 |
| 核（kernel） | 真正干活的进程：A1111、ComfyUI、自研。它自带 python 环境 |
| 管道（pipe） | 壳 ↔ 核之间的约定：怎么起、怎么判活、怎么报进度、怎么收尸 |

## 1. 目录布局

```
NeoForgeSD/
  NeoForgeSD.exe            # 壳（<10MB）
  kernels/
    fake/
      kernel.json
      fake_kernel.py
    a1111/
      kernel.json
  backend/                  # 核自己的目录，壳不管
  models/
  work/
```

## 2. kernel.json 字段

```jsonc
{
  "spec": 1,                         // manifest 版本。壳同时兼容 N 与 N-1
  "id": "a1111",                     // 唯一，= 目录名
  "name": "A1111 · NeoForgeSD 前端",  // 给人看的
  "version": "1.2.2",
  "dialect": "neoforge",             // a1111 | comfy | neoforge | custom，决定健康检查默认值
  "command": ["{python}", "ui/image_app.py"],   // 数组，不走 shell，不拼字符串
  "cwd": "{base}/app",               // 工作目录；{base} 见 §4
  "args": [],                        // 追加参数
  "env": {"PYTHONUTF8": "1", "NEOFORGE_IMAGE_PORT": "{port}"},
  "port": 8899,
  "health": {
    "path": "/api/status",           // 相对 http://127.0.0.1:{port}
    "interval_s": 1.0,
    "timeout_s": 2.0,
    "fail_threshold": 3              // 连续失败 N 次判黄
  },
  "ready": {
    "mode": "both",                  // line | poll | both；both = 谁先到算谁
    "pattern": "PIPE:READY port=(\\d+)"
  },
  "progress": {"pattern": "PIPE:PROGRESS ([0-9.]+)\\s*(.*)"},
  "needs": {"models": ["Stable-diffusion"], "vram_gb": 4},   // 环境侦探用；缺了不拦，只亮灯
  "stop": {"grace_s": 5}             // 温和终止等待秒数，超时强杀
}
```

**校验规则**（`core/kernel.py::load_manifest`，不满足直接报人话错误，不静默兜底）

- `spec` 必须是 `1` 或 `0`（N-1 兼容）；其它值 → 「这份 kernel.json 是更新版本写的，请升级启动器」
- `id` 必须等于目录名
- `command` 必须是非空数组，第一个元素必须存在（文件或可执行名）
- `port` 必须是 1–65535 的整数
- `health.path` 必须以 `/` 开头

## 3. 管道协议（壳与核之间唯一的话）

核在 **stdout** 上按行输出事件，壳逐行读：

| 行 | 含义 | 壳的反应 |
|---|---|---|
| `PIPE:READY port=8899` | 起来了，端口就绪 | 状态灯转绿，开始健康轮询 |
| `PIPE:PROGRESS 0.42 正在加载底模` | 进度 + 人话 | 转进度条 + 日志行 |
| `PIPE:LOG <任意>` | 普通日志（可选） | 原样进日志窗 |
| 其它行 | 普通 stdout | 原样进日志窗，不解析 |

**判活**：壳每 `interval_s` 请求 `http://127.0.0.1:<port><health.path>`，
连续 `fail_threshold` 次失败 → 状态黄（不立刻杀，先记日志）；
`ready.mode: poll` 时判活成功即视为就绪。

**收尸**（`stop`）：
1. 温和：先给子进程 `terminate()`（Windows 上等于 WM_CLOSE / SIGTERM 语义），等 `grace_s`
2. 强杀：`psutil` 拿整棵进程树，先 terminate 后 kill；无 psutil 时退到 `taskkill /PID <pid> /T /F`
3. 清完必须验证：端口不再 LISTEN、无残留子进程。**不留孤儿**（已知坑：杀前端连带杀后端）
4. 崩溃重启：非人为停止而退出 → 自动重启 1 次并记录原因；第二次不自动重试，把 stderr 尾部推给人

## 4. 路径占位符

| 占位符 | 解析为 |
|---|---|
| `{base}` | 壳所在目录（exe 同级，或仓库根） |
| `{python}` | `{base}/backend/python/python.exe`（存在时），否则 `sys.executable` |
| `{port}` | manifest 自己的 `port` |
| `{kernel}` | 该内核自己的目录 |

不做 `%VAR%` 展开，不调 shell，避免中文路径与引号坑。

## 5. 验收（M1）

- 假内核（`kernels/fake`）：壳能起、能判活、能收尸，全程零改动
- A1111（`kernels/a1111`）：同一套管道起来，启动器代码零改动
- 停后端口释放、无孤儿进程（`tests/test_pipe.py` 实测）
