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

## 六、第一次打开：先选界面，再补件（启动器 0.2）

**没装过后端**（`backend/python/python.exe` 不在）、也没做过选择 → 打开直接落在引导页，两张卡片：

| | WebUI（Forge，推荐） | ComfyUI |
|---|---|---|
| 是什么 | WebUI 系当前主力分支（lllyasviel/stable-diffusion-webui-forge） | Comfy-Org 官方 portable |
| 优点 | A1111 的插件（ADetailer/ControlNet/中文补全）基本能直接用；官方整合包自带 python+torch，解完就能起；界面是熟悉的 WebUI | 官方 portable 同样自带环境；工作流自由、显存更省、跟新模型快 |
| 缺点 | 本机还没实测 Forge（4 GB 参数要重调）；整合包 1804 MiB，下载/解包/首启都慢 | 没中文界面、没有一键修脸；要自己管工作流 |
| 官方包 | `webui_forge_cu124_torch24.7z` | `ComfyUI_windows_portable_nvidia.7z` |

> A1111 那份降级成下载页第三项（老牌，源码，要自己备 python+torch），不再占引导卡。

**拖整合包进来就不问这一步** —— 装完自动写 `work\setup.json`，下次直接进主界面。
想改主意就删掉 `work\setup.json`，或者点底栏「选界面」。

**下载页**（底栏「下载」）按类给：大模型（SD1.5 4.07 GB / SDXL 6.62 GB）· 小模型（VAE /
RealESRGAN / GFPGAN / CodeFormer）· 插件（ADetailer / 提示词补全 / 标签补全 / 汉化 / ControlNet）·
UI 官方整合包（Forge / ComfyUI）与 A1111 源码。选中（可多选）→「下载选中」，双击一行也行。

- 支持**断点续传**（curl `-C -` + `.part`）；服务端不认 Range 会自动清断点从头下
- 多源回退、大小校验（对不上就当失败，不留半成品）、越界包拒收
- `.7z` 用随包的 `tools/7zr.exe` 解；解完自动找 `python_embeded/python.exe` /
  `system/python/embedded/python.exe` 与入口脚本，**登记成内核**（Forge 7861、ComfyUI 8188），
  kernel.json 指向整合包自带的 python（`{base}/kernels/<id>/...`）
- **整包下不动怎么办**：本机实测官方 release CDN 只通表头，1.8/1.9 GB 整包会被掐（0 KiB/s）。
  用浏览器下完，把 `.7z` **拖进启动器窗口** —— 按文件名认得出，自动解开 + 登记内核，效果一样
- 源都验过：ModelScope（302 → CDN，支持 Range）、codeload 源码 zip、GitHub release 附件（走 curl）

## 七、打包与安装（壳自己会）

**打一份壳包**：启动器底栏点「打包」→ `work\pack\NeoForgeSD-<版本>-<时间>.zip`
（实测 75 个文件 / 12.13 MiB / 0.4 s，含 `NeoForgeSD.exe`）。包根有 `neoforge-package.json`
清单，记着每个文件的 size 与 sha256 —— 包有没有被动过，一验就知道。
包里**不含** `backend/` 与 `models/`（那是另一个量级，走 `_dist/stage_bundle.py` + 7z 分卷）。

**装一份壳包**，三条路任选：

| 怎么给 | 结果 |
|---|---|
| 把 zip **拖进启动器窗口** | 自动校验 + 装到包旁边同名目录，日志写文件数/耗时 |
| 把 zip **拖到 `NeoForgeSD.exe` 图标上** | 已在跑的那份会接单安装（单实例不破） |
| `python -m core.installer 包.zip` | 命令行同款，`--target` 可指定落点 |

规矩：**先全量校验 sha256 再写盘**（坏包不落半截文件）；`../` 越界、绝对路径、符号链接、
清单之外的文件一律拒；包里只有清单没有文件的空包也拒。装完的报告在 `work\install-<时间>.md`。

**装到已装过的目录 = 就地更新**（不是覆盖，也不是另开一份）：目录里有 `neoforge-installed.json`
账本时才这么做 ——
- 你**没动过**的文件（sha256 与账本一致）→ 更新成新包里的版本
- 你**改过**的文件 → 原样保留，报告里列出来
- 你**删掉**的文件 → 不会自己回来
- 新包里已经没有的 → 只列进「已失效」，**一个都不删**

没有账本的目录一律当陌生目录，装到旁边的 `-2` / `-3` 去，绝不动里面的东西。
包里文件名不是 UTF-8 时会自动猜编码（GB18030 / Big5 / Shift_JIS …），中文名不会变乱码。

装完还缺 `backend\` 与 `models\`（包里有源码和 exe，没有权重）—— 把旧的挪过来即可，见下一节。

## 八、从 1.2.x 升上来

1.2.x 是整合包（5.55 GiB，含 backend 与 models）。升级时**不用重下**：
把旧的 `backend/`、`models/`、`app/` 挪到新 `NeoForgeSD.exe` 同级，再放上 `kernels/` 即可。
旧 Release 仍可下载，但已标 legacy，不再引导新用户。
