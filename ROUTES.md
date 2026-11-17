# 路线契约（routes）— 与内核登记册是两层，别混

`core/route.py` 管路线，`core/kernel.py` 管内核。**它们是并列的两层，不是一层。**

| | kernel（内核） | route（路线） |
|---|---|---|
| 是什么 | 一个**可启动的推理后端进程** | 一条**生成流水线**：输入槽 → 步骤 → 输出槽 |
| 有没有端口 | 有，`port` 是必填 | **没有端口**，纯步骤组合 |
| 判活方式 | 健康检查方言（a1111/comfy/neoforge/custom） | 无（跑完就结束，或由调用方轮询） |
| 例子 | `neoforge-sd15`（SD WebUI 后端）、`sana-sprint` | `sd15-still`（出单张图）、`puppet`（立绘木偶出片） |
| 清单文件 | `kernels/<id>/kernel.json` | `routes/<id>/route.json` |

**为什么不能塞进一起**：`kernel.json` 的 `REQUIRED` 里有 `port`。给路线编一个假端口只为复用校验，
是拿语义换代码复用，迟早出事。反过来，内核也不该被当成路线——它没有输入输出槽，只有启停。

---

## 现在有哪两条

```
routes/
  puppet/route.json       立绘木偶 · 零显存 · impl.status = parked
  sd15-still/route.json   SD1.5 单张出图 · 4 GB · impl.status = active
```

刻意留了一条吃显存（4 GB）和一条不吃显存（0 GB）的：**它们不是备选关系**。
木偶可长片、可与 SD 同开；SD 出图吃满 4 GB 且一张一张来。契约必须能同时表达这两种资源画像，
否则以后加第三条（比如 ASR 转写、ffmpeg 拼接）就会被迫改 schema。

---

## 怎么写一条新路线

`routes/<id>/route.json`：

```json
{
  "spec": 1,
  "id": "my-route",
  "name": "人话名字",
  "inputs": [
    {"name": "src", "type": "image", "required": true, "desc": "..."},
    {"name": "mode", "type": "enum", "required": true, "options": ["a", "b"]}
  ],
  "outputs": [{"name": "out", "type": "video", "desc": "..."}],
  "needs": {"vram_gb": 0, "gpu": false, "requires": ["ffmpeg"]},
  "steps": [{"tool": "motion", "entry": "{impl}/motion.py", "args": {"src": "{src}"}}],
  "impl": {"status": "active", "path": "{base}/somewhere", "entry": "main.py"}
}
```

占位符：`{base}` 项目根、`{route}` 本路线目录、`{impl}` = `impl.path`。
**`{impl}` 的用处**：代码在归档里时，`impl.path` 指向归档真实位置，`step.entry` 自动跟着；
取回代码只改 `impl.path` 一处，步骤不用动。

### 契约不许撒谎

`impl.status` 只有三个值：

| 值 | 含义 | 校验要求 |
|---|---|---|
| `active` | 现役，路径就在那儿 | 路径必须存在 |
| `parked` | 代码已归档 | **必须写 `impl.path`**，指向归档真实位置 |
| `stub` | 只有想法，还没代码 | — |

写了 `active` 但路径不存在，`tests/test_route.py` 会直接失败。

---

## 校验与查看

```bash
python -m core.route            # 列出所有路线（一行一条）
python -m core.route puppet     # 打印某条的完整契约
python -m core.route --json     # 机器读
python tests/test_route.py      # 真契约 + 9 种坏契约的守门测试
```

自检里也有一项「路线登记册」：条数、契约是否合法、**哪几条代码还在归档里（用前要取回）**。

---

## 尚未实施的部分（写清楚，别当已完成）

- **没有执行器**。`route.py` 只做「登记 + 校验 + 展示」，不会真的按 steps 跑流水线。
  要跑还得 `python core_video/motion.py --sprite ... --action wave`（且代码目前仍在归档）。
- **没有变量替换到运行时的桥**。`{prompt}` / `{sprite}` 这些槽位占位符在 `steps.args` 里
  原样保留，取哪个值由调用方决定——执行器做出来之前，它们只是文档。
- 木偶路线代码仍在 `D:/AIGC/archive/2026-10/video-parked/parked_video/core_video/`。
  取回 + 跑通一条 `--action wave` 是验收契约够不够用的第一步。
