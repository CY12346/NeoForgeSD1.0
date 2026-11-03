# 内核架构 · neoforge-sd15

> 2026-11-03 · 拆分第一刀：核从壳里出来 ｜ 拆分第二刀（本文档）：核内部再分成 8 层
> 契约见 `SPEC-kernel.md`，用法见 `KERNEL-neoforge-sd15.md`，本文件只讲**骨架与规矩**。

---

## 1. 一句话

**一个进程、八个层、两类外部依赖（后端目录 / 权重目录）。**
进程里从下往上：`config → sdapi → {engine, payload, jobs, history, textfmt} → server`；
`kernel_main.py` 只在最外面负责「环境 + 管道 + 起服务」。

```
                        ┌───────────────────────────── 壳（NeoForgeSD.exe）─────────────────────────────┐
                        │  内核登记册 kernel.json · 管道 PIPE: · 状态灯 · 日志窗 · 收尸                     │
                        └───────────────▲──────────────────────────────────────────▲──────────────────┘
                        JSON/HTTP :8899 │                                          │ stdout 行
┌───────────────────────────────────────┴──────────────────────────────────────────┴──────────────────┐
│ 内核进程（{python} kernel_main.py）                                                                  │
│                                                                                                     │
│   kernel_main.py     环境自检 · PIPE:PROGRESS/READY · 起服务      ← 唯一入口，无业务逻辑              │
│        │                                                                                            │
│   ┌────▼───────────────────────────────────────────────────────────────────────────────────────┐      │
│   │ server.py      路由表 · 请求解码 · 响应编码 · /files 静态            （唯一对外说话的层）    │      │
│   └──┬──────────┬───────────────┬──────────────┬──────────────┬──────────────────────────────┘      │
│      │          │               │              │              │                                     │
│  engine.py   jobs.py        payload.py     history.py     textfmt.py      ← 业务层，互不 import      │
│  起停/判活    任务/线程      参数→载荷       历史落盘      中⇄英/词库                                 │
│      │          │               │              │              │                                     │
│      └──────────┴───────┬───────┴──────────────┴──────────────┘                                     │
│                         │                                                                           │
│                    sdapi.py    SD HTTP 客户端 + 目录缓存（唯一跟 7860 说话的地方）                   │
│                         │                                                                           │
│                    config.py   路径/端口/开关/基准目录推导（无依赖，纯读环境）                        │
│                                                                                                     │
│   _vendor/{winproc,i18n_tags}.py   第三方小件（构建时复制，sha256 守着）                             │
│   ui/image_page.html              前端页面（构建时复制）                                             │
└───────────────────────────────────────┬─────────────────────────────────────────────────────────────┘
                                        │ HTTP 127.0.0.1:7860（--nowebui）
                             ┌──────────▼──────────┐
                             │ SD 后端（A1111）    │  后端目录 + 权重目录，都靠环境变量指进来
                             │  python + torch     │  ← 内核不拥有它们，只使用它们
                             └─────────────────────┘
```

---

## 2. 为什么是「一个进程 + 内部分层」而不是拆成两个内核

| 方案 | 判断 |
|---|---|
| 前端一个内核、后端一个内核（两个 kernel.json） | ❌ 4 GB 卡只能跑一份后端，两个内核就要两次管道、两次收尸、两次端口；今晚实测的孤儿进程坑（见 §8）会翻倍 |
| 前端一个内核、后端由壳直接起 | ❌ 壳就得懂 A1111 参数，违反「壳不认识内核」 |
| **一个内核，引擎作为一层（engine.py）** | ✅ 收尸只杀一棵树；参数怪癖留在 engine；换方言只动 engine + sdapi |

结论：**进程边界只留一条（壳 ↔ 核）**，方言差异用层边界解决。以后真要拆成两个进程，
`engine.py` 的接口（`args/start/stop/status`）已经是现成的切口。

---

## 3. 八层职责（每层只做一件事）

| 层 | 文件 | 能做 | 不能做 |
|---|---|---|---|
| 配置 | `config.py` | 读环境、推基准目录、算端口、VAE 推断 | import 本包其它层；起进程；发请求 |
| SD 客户端 | `sdapi.py` | 跟 7860 说话、缓存目录（60 s）、判活 | 起停后端；解析页面参数 |
| 引擎 | `engine.py` | 后端进程起停、启动参数、日志文件、状态机 | 处理生成任务；碰页面 |
| 任务 | `jobs.py` | job 注册表、工作线程、落盘、写历史 | 解析 HTTP；拼载荷 |
| 载荷 | `payload.py` | 页面 dict → SD API dict（纯函数） | 网络、文件、时间 |
| 历史 | `history.py` | 读/写 `work/history_image.json`（原子写） | 知道是谁在调 |
| 提示词 | `textfmt.py` | 中⇄英、补全、词库；词典坏了降级成 503 + 原因 | 影响出图主链 |
| HTTP | `server.py` | 路由、编解码、静态文件、边界校验（body 上限 64 MB） | 写业务逻辑；直接碰子进程 |

**依赖方向只有一条**：上面可以 import 下面，下面绝不可以 import 上面。
`tests/test_kernel_sd15.py` 的 ①b 组用正则扫 `config.py` / `sdapi.py`，出现反向 import 直接判失败；
同时断言 `kernel_main.py` 只 import `server` 与 `config`。

---

## 4. 一次出图的时序

```
页面 POST /api/image {prompt, width, steps…}
  └─ server.do_POST
       ├─ sdapi.alive() ？      没起来 → 503「SD 后端未启动，先点『启动后端』」
       ├─ payload.build_txt2img 参数 → 载荷（缺提示词 → 400）
       └─ jobs.submit → 立刻返回 {job_id}            ← 页面不卡在请求里
             └─ 线程 jobs._run
                  ├─ sdapi.post /sdapi/v1/txt2img  （阻塞到出图完）
                  ├─ base64 解码 → work/<job>_00.png
                  ├─ history.add(...)  失败只记 history_error，不影响出图
                  └─ job.state = done | error（带 error 字符串，人话）
页面轮询 GET /api/job/<id> → done → 读 /files/<name>
```

后端起停是另一条独立链路：`POST /api/start_sd` → `engine.start()`（立刻返回）→ `engine._boot`
线程里 `winproc.popen(webui.py)` → 每 2 s `sdapi.alive()`，最多 8 分钟 → `START.state` 变 ready/failed。
页面靠 `GET /api/status`（也就是壳的健康检查）看这条进度。

---

## 5. 扩展点（改哪里，不改哪里）

| 想干的事 | 改哪 | 不用改 |
|---|---|---|
| 接 ComfyUI / 自研后端 | `engine.py`（启动参数、进程管理）+ `sdapi.py`（路径与方言） | `server.py` / `jobs.py` / 前端 / 壳 |
| 加一个新接口 | `server.py` 加一条路由 + 需要的话在下层加一个函数 | 壳、kernel.json |
| 换前端页面 | 改仓库 `ui/image_page.html` → `build_kernel.py` 重新 stage | Python 全不动 |
| 换壳（别的启动器） | 不用改 | 只要它认 spec v1 |
| 换模型/权重目录 | 环境变量 `NEOFORGE_SD_HOME` | 代码全不动 |
| 调 4 GB 卡参数 | `engine.SD_ARGS`（或用 `NEOFORGE_SD_MEDVRAM/SAFE_VAE/TOME` 开关） | 其它层 |

---

## 6. 边界：内核明确不做什么

- 不装 python / torch（GB 级 + 版本地狱），只要带 `python/` 的后端目录
- 不下载模型、不管权重组织（`models/` 是外部资产，只通过后端读）
- 不读 `work/setup.json`、不做首启引导（那是壳的事）
- 不打印业务日志到 stdout 里乱插手：管道行只有 `PIPE:PROGRESS/READY/LOG` 三种，
  后端日志落到 `work/sd_server.log`，前端 httplog 静音
- 不做鉴权/多用户：只绑 `127.0.0.1`，本机单人用

---

## 7. 三种文件的归属（别搞混）

| 类别 | 例子 | 谁改 | 进不进 git |
|---|---|---|---|
| 手写源码 | `neoforge/*.py`、`kernel_main.py`、`kernel.json` | 人 | 进 |
| 复制品（构建 stage） | `neoforge/ui/image_page.html`、`neoforge/_vendor/{winproc,i18n_tags}.py` | `tools/build_kernel.py` | 进（带 sha256 守卫，防岔开） |
| 构建产物 | `manifest.json`、`dist/kernels/*.zip` | 构建脚本 | manifest 进，zip 不进（dist 已 ignore） |

---

## 8. 今晚踩到并写进架构的坑

| 坑 | 现象 | 现在怎么防 |
|---|---|---|
| 父进程 terminate 后反查子进程 | `psutil.Process(pid).children()` 抛异常 → 空列表 → **SD 后端活着占 4 GB 显存** | `core/pipe.py` 改为用 stop 开头抓好的 pids 杀；`tests/test_pipe_killtree.py` 守 |
| 拆层后两份代码悄悄岔开 | 改了仓库 `ui/` 忘了重建，内核还在跑旧页面 | `build_kernel.py --verify` + 测试里比 3 个复制品的 sha256 |
| 层反向依赖 | 下层为了图省事 import 上层，拆了等于没拆 | 测试 ①b 用正则扫反向 import |

---

## 9. 验收（2026-11-03 实测）

| 项 | 数字 |
|---|---|
| 包内文件 | **16 个 / 109.67 KiB**（zip **43.38 KiB**，sha256 `f57393a4…10eec`） |
| 手写源码 | 13 个 `.py`（8 层 + `__init__` + `_vendor/__init__` + `kernel_main`） |
| 起核 | **2.7 s** 到绿（管道 `PIPE:READY` + `/api/status`） |
| 接口面 | 首页 200 / 47,864 B；`/api/{catalog,history,styles,progress,i18n}` 全通；不存在的 job、文件 404；后端没起时 `/api/image` 503 说人话 |
| 端到端 | 后端上线 30.4 s（模型已缓存）· 512² 出图 **5.2 s** · 378,349 B · 收尸干净（3 个后代全死、端口释放） |
| 回归 | `tests/test_kernel_sd15.py` **32 项断言全过** · `test_pipe_killtree.py` 全过 · `test_launcher_smoke.py` SMOKE OK |

---

## 10. 已知技术债（下一刀可选）

1. `ui/image_app.py`（730 行单体）还在，给老的 `kernels/a1111` 用；等 a1111 归档后可以删掉，仓库只剩一套实现。
2. `payload.py` 里有 4 GB 卡专属参数（`s_min_uncond`），严格说属「引擎方言」，ComfyUI 接入时应该挪进 engine 的 profile。
3. 没有统一 logging：现在是 `work/sd_server.log` + 管道行，缺一个结构化 `work/kernel.jsonl`。
4. `neoforge-sd15` 与 `a1111` 同端口 8899，只能二选一（管道会拒第二份）。
