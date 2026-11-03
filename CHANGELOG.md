# 更新日志

## 1.4.1 ｜ 2026-11-04 收掉内核登记册里的两条历史包袱

用户问「里面为啥还有一个 A1111（绘世）启动器」——那其实是 1.2.2 时代的老内核
`kernels/a1111`（`command` 直接指向 `ui/image_app.py`），跟 `neoforge-sd15` **端口都是 8899**、
功能完全重叠。更糟的是启动器 `selection_set(0)` 按名字排序选中它，等于默认用了老的那条。

- `kernels/a1111` → 归档到 `_parked/kernels/a1111`（归档不删除）
- `kernels/fake` 加 `"hidden": true`：管道自测还要用它，但不再出现在界面列表里
- 默认选中改成优先 `neoforge-sd15`（找不到才退回第一个）
- 回归：`test_pipe` / `test_launcher_smoke` / `test_kernel_sd15` 全过，界面列表现在只剩
  `neoforge-sd15` + `sana-sprint` 两个真内核

## 1.4.0 ｜ 2026-11-04 已发版（tag v1.4.0，release 带 exe）

**干净安装验证**：本地 `app/` 清空 → 用 codeload 下源码 zip（119 文件）、用
`gh release download` 下 exe（12,918,049 B，sha256 `d24b7d99…`），重装后：
exe 起来打 `NeoForgeSD 1.4.0 启动器已就绪`、认出 4 个内核；源码跑
`e2e_kernel_shell.py` 全通（起核 2.6 s · 后端 34.4 s · 出图 12.2 s · 收尸 4.1 s，
383 KB 落盘）。也就是说这包不依赖本地构建产物。

**这版装了什么**：
- 下载页接魔搭在线目录，289 条（14 内置 + 275 在线），类型列细分成
  「底模 · SD1.5 / SDXL / FLUX / LoRA / VAE / ControlNet / 放大 / 修脸」
- 插件与 UI 本体按 GitHub 热度排行：A1111 ★16.5万 > ComfyUI ★14.5万 > ControlNet
  ★1.8万 > Forge ★1.3万 > ADetailer ★4.8k > 提示词补全 ★3.2k > 标签补全 ★2.8k > 汉化 ★479
  （冷启动用内置实测值，7 天后后台刷新 `work/popularity.json`）
- 第二内核 `kernels/sana-sprint`：`PIPE:READY` + 出图 14.3 s / 峰值 4.13 GiB 实测通过，
  端口 8898，跟 sd15 并存**不替换**（理由见下一条 CHANGELOG）

**发布踩到的坑**：`tools/_push_via_api.py` 在 `.github/workflows/ci.yml` 上报 404 ——
推 workflow 文件要 `workflow` scope，当前 token 只有 `repo/gist/read:org`。已在脚本里
把 `.github/` 排除，CI 那条待办仍挂着（`gh auth refresh -h github.com -s workflow`）。

## 未发布 ｜ 新架构调研：SANA-Sprint 能跑，但不值得换掉 SD1.5 内核

问题：网上有没有新的生图架构，能替掉 `kernels/neoforge-sd15`，至少保住 SD1.5 的生图能力。

**筛完只剩一个候选**：FLUX.2 [klein] 4B 官方标 8.4 GB 显存、Z-Image-Turbo 11.7 GB 起，
这台 4 GB 卡都装不下；SANA（NVIDIA，Linear DiT + DC-AE 32× 压缩）是唯一宣称
「4-bit 量化后 < 8 GB 可跑」的，且有 0.6B 版本。

**实测（D:\AIGC\sana-probe，真跑出来的）**：

| | SANA-Sprint 0.6B（2 步） | SD1.5 现在（A1111 28 步） |
|---|---|---|
| 512² | 文本 3.3 s(CPU) + 出图 7.7 s = **11 s** | **5.7 s** |
| 1024² | 文本 3.4 s + 出图 47.9 s = **51 s** | **37.7 s** |
| 峰值显存 | 512² 4.13 GiB · 1024² **6.19 GiB** | 约 3.6 GiB，不爆 |
| 模型体积 | 7.17 GiB | 4.07 GiB（emaonly） |
| 中文提示词 | 「红熊猫咖啡师」画成了大熊猫（场景/暖光对，物种错） | 靠 i18n_tags 译成英文，稳 |

**结论：不换。** 三条硬理由：① 在我们这台卡上比 SD1.5 慢（文本编码器是 Gemma-2B 4.76 GB，
4 GB 显存装不下只能留 CPU，每次出图固定多 3.3 s；1024² 峰值 6.19 GiB 直接爆，走内存回退）；
② SANA 不认 SD1.5 的生态——LoRA、ADetailer、ControlNet、8.6 万条中文 tag 库全部作废；
③ 画质确实高一个档次（2 步就出很干净的片子，见 `D:\AIGC\sana-probe\out\`），但要为它重做整套资产。

**留着当第二内核的料**：`kernels/` 天生支持多内核并存（`kernel.json` 一放就登记），
真要做「高质量单张」时可以把 SANA 包成 `kernels/sana-sprint`，跟 sd15 并存，不替换。

**踩到的坑**（写进 `D:\AIGC\sana-probe\probe.py` 注释里了）：
- diffusers 0.34 在 import 时读 `torch.xpu`（torch 2.6+ 才有），torch 2.2 要打空壳补丁
- `dtype=` 不是 `SanaSprintPipeline.from_pretrained` 认的参数，会被静默忽略
- pipeline 的 `_execution_device` 跟着 text_encoder 走（在 CPU）→ 自动生成的 latents 落 CPU，
  跟搬到 GPU 的 transformer 打架；要把类属性钉成 cuda，并且显式传 latents
- CPU 上 fp16 是软件模拟，Gemma-2B 编码 5 分钟都出不来；转 fp32 后 3.3 s

## 未发布 ｜ 内核 ↔ 外壳连接性：端到端跑通（新测试 e2e_kernel_shell.py）

不 mock，真进程真 HTTP：

- **起核** 2.6 s（收到 `PIPE:READY port=8899`，状态 green）
- **内核 → 后端** `POST /api/start_sd` → A1111 就绪 28.9 s
- **出图** `POST /api/image`（512² / 8 步）→ `GET /api/job/<id>` → **5.1 s** 完成
- **产出** `work/5339bbcaf589_00.png` 383 KB，真落盘
- **收尸** 3.9 s，进程树 4 个全清、端口释放

顺带修了两个测试自己的 bug（都是断言写错，不是产品问题）：`kernel.discover` 要显式传
`base_dir`（内核在 `app/kernels`，落点根在上一级，和启动器 `discover_all()` 同一套传法）；
job 的状态字段是 `state` 不是 `status`。

## 未发布 ｜ 下载页能「获得」模型数据了：接上魔搭的公开搜索

之前下载页只有 14 条写死的条目，类别对了、数量太少。现在接上魔搭（ModelScope）的
公开接口，能在界面里搜、能把结果并进清单、能按类型看：

- **实测能用的源**（2026-11-03 逐个验）：`modelscope.cn/openapi/v1/models` 搜索（200，
  参数是 **snake_case**：`search` / `page_number` / `page_size` / `sort`，驼峰会被静默忽略 ——
  这一条坑了半天）、单仓库详情、`/repo/files?Recursive=true` 文件树（带精确字节数）。
  同一轮验死：**civitai.com 与 huggingface.co 本机 15 s 超时**，`/api/v1/models?...` 与
  `dolphin/models` 全是 404（列表接口已下线或要登录），github.com 443 仍然 reset。
- **搜索一次就够**：openapi 每条已带 `display_name` / `tags` / `file_size` / `downloads`，
  不用逐条问详情；**具体下哪个文件**（`pytorch_lora_weights` 这种千篇一律的名字要改成仓库名）
  留到下单时问一次文件树（`hydrate`），清单加载因此不慢。
- **类型判定按可靠度排**：`custom_tag:Checkpoint` 是权威的模型类型，`library:lora` 只是
  「能用 lora 库加载」—— 先看前者，否则 SD1.5 底模会被后者带跑（MusePublic 那几个就是）；
  再加名字里的 `_ckpt_` / `_lora_` 与 4 GiB 体积兜底。实测 275 条：底模 85、LoRA 56、
  ControlNet 27、VAE 24、SDXL 20、FLUX 20、放大 14、SD1.5 13。
- **界面**：类型列从「大模型/小模型」细分成「底模 · SD1.5 / LoRA / VAE / ControlNet / 插件 · 修脸」，
  多了「来源」列（内置 / 魔搭）、搜索框、「搜魔搭」、「粘贴链接添加」（认 URL 也认 `owner/name`）。
  下载页 14 条 → **289 条**（14 内置 + 275 在线）。
- **缓存**：`work/catalog-online.json`，跟内置条目合并去重；`core/catalog.py::load()` 负责合并，
  在线清单坏了就当没有，不拖死下载页。
- **回归**：`tests/test_msremote.py`（搜索/缓存合并/hydrate/类型判定/链接解析，含「没有 WebUI
  权重的 GGUF 仓库不瞎编 URL」），`tests/test_setup_page.py` 改成断言细分类型列。
- **没做的**：`app/dist/NeoForgeSD.exe` 还是 11-01 的旧版，功能只在源码里；要发版得重打包。

## 未发布 ｜ 内核架构：neoforge-sd15 内部拆成 8 层

壳那刀（下面那段）之后，核内部再拆一刀：**一个进程、八个层、单向依赖**。
设计写在 `KERNEL-ARCH.md`，代码在 `kernels/neoforge-sd15/neoforge/`。

- **8 层**：`config`（环境/路径）→ `sdapi`（唯一跟 7860 说话）→ `engine`（后端进程生命周期）/
  `payload`（参数→载荷，纯函数）/ `jobs`（任务与线程）/ `history`（原子写）/ `textfmt`（中⇄英）→
  `server`（唯一的 HTTP 入口）。`kernel_main.py` 只剩环境自检 + PIPE 行 + 起服务
- **依赖方向单向**，测试用正则扫 `config.py`/`sdapi.py` 有没有反向 import；`kernel_main` 只许
  import `server` 与 `config`
- **复制品只剩 3 件**（页面 + winproc + i18n_tags），旧布局的 `app/` 由构建脚本自动清掉；
  包从 8 文件/105 KiB 变 **16 文件/109.67 KiB**，zip **43.38 KiB**（sha256 `f57393a4…10eec`）
- **接口面一个没少**：首页 200/47,864 B，`/api/{catalog,history,styles,progress,i18n}` 全通，
  不存在的 job/文件 404，后端没起时 `/api/image` 503 说人话；body 上限加了 64 MB（413）
- **实测**：起核 **2.7 s** 到绿；端到端后端上线 30.4 s、512² 出图 **5.2 s**、收尸干净（3 个后代全死）；
  `tests/test_kernel_sd15.py` **32 项断言全过**（新增 ①b 分层组与 ②b 接口面组）
- **明确不拆**：不拆成「前端内核 + 后端内核」两个进程——4 GB 卡只能跑一份后端，两个内核就两次
  收尸两次端口，今晚刚踩过的孤儿坑会翻倍。以后真要拆，`engine.py` 的 `args/start/stop/status`
  就是现成切口

## 未发布 ｜ 修：收尸漏掉内核自己拉起的子进程（4 GB 显存就是这么被吃掉的）

`core/pipe.py::_kill_tree_psutil()` 旧实现到「父进程已退」那一步才去
`psutil.Process(self.proc.pid).children()` —— Windows 上父进程 terminate 后立刻消失，这里必抛异常、
退成空列表，于是 **SD 后端（7860）活着**。今晚端到端实测漏了两次：`netstat` 一直 LISTENING，
4 GB 显存被孤儿占着，下一次起核直接 OOM。

改法：用 `stop()` 开头就抓好的那份 pids 去杀（后进先出，先杀深层），不再反查；
自报状态也跟着修（`clean=True` 只在 pids 全灭 + 端口释放时才给）。
回归 `tests/test_pipe_killtree.py`：假内核自己 spawn 一个 sleep 子进程 → stop 后必须死。

## 未发布 ｜ 内核拆分：neoforge-sd15 独立内核 + 内核文档

出图那一半从壳里拆出来了：**代码归核、环境靠 env 声明**。壳还是那 12.5 MB 的 exe，核是
`kernels/neoforge-sd15/` 一个目录（8 个文件 / 105 KiB / zip 37 KiB）。

- **入口 `kernel_main.py`**：查 `NEOFORGE_SD_HOME` 与后端 `python.exe` → 缺就说人话退 **3**；
  就绪时在端口**真正绑上**之后才打 `PIPE:READY port=8899`（不是进程一起来就报假绿）；
  代码自己往上找 `backend`（最多三层），所以不带壳也能单独跑
- **代码进核**：`app/ui/image_app.py`（730 行）+ `image_page.html` + `app/core/{winproc,i18n_tags}.py`
  由 `tools/build_kernel.py` 从仓库 `ui/`、`core/` stage 过来；`manifest.json` 记 8 个文件的 size+sha256
- **分发包**：`dist/kernels/neoforge-sd15-1.3.0.zip`（37.21 KiB）· sha256 `c1b480c5…70f4`；
  丢进任何认 spec v1 的壳的 `kernels/` 就能起，壳零改动
- **防岔开**：`tests/test_kernel_sd15.py` 第①组就比「核里的代码副本 vs 源」的 sha256，
  改了源忘了 rebuild 直接失败
- **内核文档** `KERNEL-neoforge-sd15.md`：边界、依赖、三种起法、环境变量、管道行、HTTP 接口、
  故障表、构建/分发、实测记录
- **实测**：壳管道拉起 **4.6 s** 到绿；`/api/status` 回报的 `sd_home` 就是壳给的那份；
  停后端口释放、`kernel_main.py`/`webui.py` 残留 **0**；缺环境退出码 **3**；
  `build_kernel.py --verify` 8 个文件全对；端到端真出图见文档 §11


## 未发布 ｜ 0.2 修正：引导页排版 —— 自己排行，别指望 Tk 的 wraplength

引导页两张卡的「优点 / 缺点」两列，在窄窗口下会被窗口右缘切掉、首行还会出现
一个孤零零的英文单词。查下来是 Tk 的排版规则：

- `wraplength` 只在空格处断行；中文一整段被当**一个词**，塞不下就整段挪到下一行再硬切
  → 所以「WebUI 系现在的主力分支…」会变成第一行只有 `WebUI`、第二行一大坨
- 插零宽空格（U+200B）想给 Tk 提供断点 —— **Tk 8.6 不认**，白插（还会污染复制出来的文本）
- 控件请求宽度仍按最长行算，卡片一路把窗口撑出去，右边就被切

改法：`launcher/neoforge_launcher.py` 新增 `hard_wrap(text, width)`，**自己贪心排好行再塞真换行** ——
英文单词（含 `_./+-%`）整体不拆，汉字按 2 个单位计，行宽 46 个单位（约 23 个汉字）；
左列标题/按钮文案用 26 个单位。标签的 `wraplength` 全部设 0，只留 `width` 掐请求宽度。

- 顺带修 `show_setup()` 撑窗口时的读数：窗口还没映射时 `winfo_width()` 返回 1，
  原逻辑会把用户已经拉到 1400 宽的窗口压回 980 —— 现在读不到就用 geometry 里的目标尺寸
- 实测：1400×820 下两张卡完整、优缺点不再被切、底部「我有整合包」按钮可见
  （`E:\NeoForge\work\doc-figs\setup.png`）
- 回归：`test_setup_page`（16 项断言）· `test_launcher_smoke` · `test_pack` · `test_install_merge` 全绿

## 未发布 ｜ 0.2 修正：底模就两条路 —— WebUI(Forge) / ComfyUI，官方整合包直接取

**用户口径**：不用 A1111 那份；底模就是 WebUI 或 ComfyUI，直接去它们官网取。

- **引导页两张卡改名改内容**：`A1111 WebUI` → **WebUI（Forge）**（lllyasviel/stable-diffusion-webui-forge，
  WebUI 系的活跃分支），ComfyUI 不变；优劣重写 —— Forge 侧写「插件兼容 + 官方整合包自带 python/torch，
  解完就能起」，并**明说本机还没实测 Forge**，A1111 的老数字只作同源参考
- **UI 本体改成官方 release 整合包**（自带 python + torch，不再是"要自己配环境"的源码）：
  - `webui_forge_cu124_torch24.7z` 1804 MiB（探测：支持 Range）
  - `ComfyUI_windows_portable_nvidia.7z` 1945 MiB（探测：支持 Range）
  - A1111 降级为第三项「老牌，源码」，不占引导卡
- **.7z 解包**：随包带 `tools/7zr.exe`（602 KB，7-Zip 26.04 x64，Public Domain），
  `install_archive7z()` 先解到 `.unpack` 临时目录再逐个搬（搬的是我们自己控的路径，越界直接拒）
- **自动认环境**：解完自己找 `python_embeded/python.exe`（ComfyUI portable）、
  `system/python/embedded/python.exe`（A1111/Forge 新布局）与 `main.py`/`launch.py`，
  写出的 kernel.json 用 `{base}/kernels/<id>/...` 指向整合包自带的 python，换机器不废
- **下载器换 curl 后端**：实测 GitHub release 附件在 urllib 下 TLS 握手被掐
  （`_ssl.c:983 handshake timed out` / `WinError 10054`），换 `curl -L --fail -C -` 才通；
  进度靠盯 `.part` 大小；curl 退出码 33/36（服务端不认 Range）→ 自动清掉断点从头下
- **拖进来也能装**：不是我们的 zip 包时，按文件名认目录项（精确名或前两段前缀，
  如 `ComfyUI_windows_portable_nvidia.7z`）→ 自动解开 + 登记内核
- **实测（2026-10-31）**：
  - 探测：Forge **1804 MiB / Range=True**（13.6 s）、ComfyUI **1945 MiB / Range=True**（2.9 s）
  - **整包传输在本机被掐**：8 MiB 分段实测 Forge `curl rc=35 SSL 53 s 0 B`、
    ComfyUI `rc=28 超时 71 s 1 KiB` —— 目录里已写明这条，并给出「浏览器下完拖进启动器」这条路
  - 7z：造包 → 解包 → 剥顶层 → 认出 `python_embeded/python.exe` + `launch.py`/`main.py`
    → 写出 `{base}/kernels/comfyui/python_embeded/python.exe` 的内核，全过
  - `tests/test_downloader.py` 扩到 12 组（续传/多源/大小/无 Range/zip 解包/7z 解包/认包/内核）

## 未发布 ｜ 启动器 0.2：第一次打开先选界面（A1111 / ComfyUI），加下载页

**需求**：第一次用要能选走哪条路（两个选项下面写清优劣）；直接拖整合包进来就不该问这一步；
另外要能在启动器里下 大模型 / 小模型 / 插件 / webui / comfyui。

- **首启引导页**：`work/setup.json` 是"做过选择"的凭证。没有它、并且没有
  `backend/python/python.exe` → 打开直接落在引导页；否则照旧进主界面（不拿引导页糊老用户的脸）
- **两张卡片**：A1111 WebUI（4 条优点 / 3 条缺点，标"推荐"）与 ComfyUI（4/3），
  优点缺点是这台机器上实打实跑出来和踩过的（ADetailer 9 s/张、冷启 143 s、SDXL 7.3 分钟、
  ComfyUI 没验过 4 GB 参数…），不写宣传话。底部还有「我有整合包，等会儿自己拖进来」
- **拖整合包不触发引导**：`_install_done` 成功即 `write_setup("package", version=…)`，
  下次开不再问（实测：装完 setup.json 变 package，再调首启逻辑也不跳页）
- **下载页**（底栏「下载」）：5 类 13 项目录，分类筛选 + 多选 + 双击即下 + 进度条 + 停止
  - 源全部本机实测过：ModelScope 文件接口（探测 SD1.5 = **4067.6 MiB · 支持 Range**）、
    codeload 源码 zip、github release 附件（跟随跳转，206 + 实收字节）
  - `core/downloader.py`：断点续传（`.part` + Range）、多源回退、大小不符拒收、
    服务端不支持 Range 时从头下并说明、安全解包（复用 installer 的越界/符号链接检查）、
    剥掉仓库 zip 的顶层目录；下完 ComfyUI 顺手写 `kernels/comfyui/kernel.json`
    （端口 8188 · `/system_stats` · poll 模式）
- **实测**：
  - 断点续传：下到 2 MB 取消 → `.part` 留 2,097,152 B → 重下发 `Range: bytes=2097152-`，
    接上后内容 sha256 与源一致
  - 多源回退：第一个源 404 → 自动换源成功；大小声明不符 → 报「大小不对」且不留成品文件
  - 真下载：ADetailer 源码 68 KB / 4.1 s → 解出 44 个文件（`install.py`、`scripts/` 都在）
  - 越界包（`top/../../escape.txt`）→ 拒；ComfyUI 内核登记后 `kernel.discover` 能读出来
  - 测试：新增 `tests/test_downloader.py`（本地 HTTP 服务 + 真 Range，9 组用例）、
    `tests/test_setup_page.py`（真窗口：首启落页 / 优劣齐全 / 选完预选下载 / 装包后不再问）
  - 回归：`test_launcher_smoke` 仍绿（主界面、内核登记册、侦探没被新页面影响）
- **明确没做**：不自动装 python + torch 运行时（GB 级 + 版本地狱，INSTALL.md 写清就够）；
  不放 github.com 直连（本机 443 被 reset）；目录是人工验过的清单，可用 `work/catalog.json` 覆盖

## 未发布 ｜ 打包/安装第二轮：照桌面那两个 MC 启动器（HMCL / PCL2）的规矩补硬底子

**来源**：`E:\我的世界\HMCL-3.16.4.exe`（开源，读了 Zipper / Unzipper / CompressingUtils /
ModpackInstallTask 的真代码）与 `Plain Craft Launcher 2.exe`（闭源，只能读它导出的行为与文案）。
**只学思路，没搬代码**；HMCL 是 GPL-3.0，要搬得跟着开源，这里不沾。

- **文件名编码要猜**（抄 `CompressingUtils.findSuitableEncoding`）：老压缩工具不写 UTF-8 标记，
  中文名会变成乱码路径。现在按 UTF-8 → 本机编码 → GB18030 → Big5 → Shift_JIS → cp1252 逐个
  **严格解码**，全部通过才采用；猜出来的编码写进日志。实测：手搓一个 GBK 文件名的包
  （`app/说明.txt`）→ 猜成 `gb18030` → 正常落到中文路径
- **覆盖已有安装走三态合并**（抄 `ModpackInstallTask` 的 overwrite 过滤）：磁盘 sha256 与
  `neoforge-installed.json` 账本一致 = 用户没动过 → 更新；不一致或账本里没有 = 保留用户的；
  账本里有但磁盘没了 = 用户删的 → 不复活；新包里没有的只列进「已失效」，**不删**
- **包旁边同名目录里有账本 → 就地更新**，没有账本（陌生目录）→ 照旧让路 `-2/-3`，绝不动
- **显式目录条目**（抄 `Zipper.preVisitDirectory`）：包内 15 个目录条目，7-Zip / 资源管理器
  都认得出空目录；文件时间戳跟着包走（`ZipInfo.from_file`，装完 `os.utime` 还原，实测 22:46:14 一致）
- **空包拒绝**（抄 `Unzipper` 的 `entryCount == 0` 判据）：只有清单没有文件的包不再"装成功"
- **包内自带 `README-先看我.txt`**（PCL2 的思路：整合包要能自己说清怎么起，没启动器的人也能用）
- **撞名条目 `.1`…`.9` 再报错**（抄 `Zipper.newEntry`），并保留 `.DS_Store` 等跳过规则
- 实测（真包 77 个文件 / 12.20 MiB / 0.1 s）：
  - 新装 77 个文件 0.2 s；改 1 个文件 + 删 1 个文件后再装 → **写入 75（新 0 更新 75）、
    保留 1、未复活 1**，改过的内容还在、删掉的没回来
  - 新增 `tests/test_install_merge.py`：GBK 名字 / 空包拒绝 / 三态合并（改·删·更新·失效不删）
    / 账本记录 —— 全过；`test_pack`、`test_pack_drop`、`test_launcher_smoke`、
    `test_no_console` 回归全绿

## 未发布 ｜ 底栏「打包」+ 拖进来就自动装（壳自己会分发了）

**需求**：启动器要能一键把这份壳打成包；打好的包拖进启动器就自动安装。

- **`core/pack.py`（新）**：`pack.build()` 把 `app/` 源码（含 `kernels/`）打成 zip，有
  `app/dist/NeoForgeSD.exe` 就顺手带上（放包根，双击即用）；跳过
  `build/ dist/ work/ outputs/ _parked/ __pycache__/ .git/ .github/ node_modules/ *.log *.pyc *.bak*`。
  包根写 `neoforge-package.json` 清单：schema / 版本 / 时间 / **每个文件的 size + sha256**。
  进度按字节回调；默认落 `work/pack/NeoForgeSD-<版本>-<YYYYmmdd-HHMM>.zip`
- **`core/installer.py`（新）**：认包 → 校验 → 解包。**先全量校验 sha256 再动盘**（坏包不落半截文件）；
  zip-slip（绝对路径 / 盘符 / `..` / 符号链接）一律拒；清单之外的文件也拒；目标盘空间不够不开工；
  默认落点 = 包旁边同名目录，重名让路 `-2/-3`，**绝不覆盖**；出 `work/install-<时间戳>.md` 报告
- **`core/droptarget.py`（新）**：`WM_DROPFILES` 走 ctypes —— `DragAcceptFiles` + 换掉窗口过程
  （只捞拖放消息，其余原样 `CallWindowProc` 回给 Tk），路径回调走 `after_idle` 回主线程
- **启动器**：底栏 `图库 / 维修 / 打包` 三个按钮；`打包` 走后台线程 + 橙色进度条 + 日志。
  拖进窗口 → 自动校验 + 安装，装完只报路径不啰嗦。不是包的文件（txt、普通 zip、`.7z.001` 分卷）
  一律不装，日志写清为什么
- **拖到 exe 图标上也能装**（单实例不破）：第二份进程把包路径写进 `work/drop-inbox/`，
  已在跑的那份 1.5 s 内取走安装，自己退出
- **实测**（2026-10-31）：
  - 点「打包」：**75 个文件 / 12.13 MiB / 0.4 s**（含 exe 11.55 MiB），回读 `pack.verify`
    **75 个文件 size + sha256 全对**；包里无 `work/ dist/ *.bak` 泄漏
  - 拖进去：真 `WM_DROPFILES` 消息 → 校验 + 装 **75 个文件 0.2 s**，目标目录结构完整
  - 拖到 exe 上：第二份 **0.2 s 内转交并退出**，第一份 1.5 s 轮询内接单、**0.2 s 装完**
  - 拒装实测：改 1 个字节的包（sha256 对不上，且目标目录零文件）、`../` 越界包、普通 zip、
    `.7z.001` 分卷、不存在的路径 —— 全部拒绝并说人话
  - 测试：`tests/test_pack.py`（建包/校验/安装/重名让路/拒坏包/接力箱）、
    `tests/test_pack_drop.py`（真窗口 + 真拖放消息 + 接力箱，全流程 2.4 s）
  - 回归：`test_launcher_smoke` / `test_no_console` / `test_repair` / `test_repair_in_launcher` /
    `test_single_instance_e2e` / `test_gallery_page` 全绿；期间新增控制台窗口 0 个
- **不打包 backend/models**（225 GB），那条路仍是 `_dist/stage_bundle.py` + `7zr -v1900m` 分卷；
  壳装完只带源码与 exe，权重与后端要单独备
- **命令行同款**：`python -m core.pack [--verify]` / `python -m core.installer 包.zip [--target D:/x]`

## 未发布 ｜ 「图片」改名「图库」：启动器里直接看最近出图，按日期分组

**需求**：底部「图片」原来只是拿资源管理器打开出图目录；要改成点一下就在启动器里看图，按时间分类。

- 底部按钮 `图片` → `图库`，点击不再 `os.startfile` 打开目录，而是把主界面换成图库页
  （`page_main` / `page_gallery` 两个兄弟页，顶栏常驻），页内 `← 返回` 回主界面，主界面状态原样保留
- 扫 `kernel.json` 的 `outputs` 目录（递归，png/jpg/jpeg/webp/bmp/gif），按 mtime 倒序取最近 200 张
- 按天分组：组头 `今天 · 10月31日` / `昨天 · 10月30日` / `2025-9月7日`，组内按时间倒序
- 缩略图落 `work/thumbs/<md5>_156.png`（路径+时间戳做键，同一张只做一次）；有 PIL 用 LANCZOS，
  没 PIL 的便携包退回 Tk 直读 PNG + 整数倍 `subsample`
- 扫盘与缩略图在后台线程，主线程分片加载（一次 8 张），窗口不假死；列数按画布宽自适应
  （900 宽 4 列、1020 宽 5 列），缩放窗口 300 ms 去抖重排
- 点任意格子用系统默认程序打开原图；`刷新` 重扫、`打开文件夹` 保留原来的资源管理器行为
- 顺手修两个挡路的布局坑：`pack` 里带 expand 的标签会把右侧按钮挤成 0 宽；`pack` 里 canvas 会把
  滚动条挤成 0 宽 —— 两处都改 grid
- **顺手修一个老 bug**：`kernel_roots()` 只找 `BASE_DIR/kernels` 和 `app/kernels`，漏了 pyinstaller 的
  `_MEIPASS` —— 便携包双击后内核登记册是空的（图库也就无从取 outputs）。补上第三处后 exe 认出 2 个内核
- 实测：
  - `tests/test_gallery_page.py`：200 格 / 9 个日期分组 / 缩略图 100% / 列数自适应 4 列 / 重入不炸 /
    无 PIL 兜底也出图（128×64）
  - 便携包 exe 12.11 MB（+0.36 MB）：截图核对图库页标题、`共 373 张 · 最近 200 张`、日期分组、
    右侧滚动条、4 列无裁切；主界面底部为 `图库` / `维修`
  - 回归：`test_launcher_smoke` / `test_no_console` / `test_single_instance_e2e` /
    `test_repair_in_launcher` / `test_pipe` / `test_singleton` 全绿
- 已知：便携包没打 PIL，JPEG 缩略图显示「无法预览」（PNG 正常，SD 默认出 PNG）；要全格式可把 PIL
  打进 spec（约 +6 MB）

## 未发布 ｜ 维修收进启动器 + 全程不弹 cmd 黑框

**问题**：启动器是 pythonw（没有控制台），里面每调一次 `taskkill` / `netstat` / `tasklist` /
`nvidia-smi` / `python -c`，屏幕上就闪一个黑框；桌面上那个「维修」还是 `python -m core.repair`
的 `.cmd`，双击直接顶着一个控制台窗口。

- **`core/winproc.py`（新）**：`winproc.run()` / `winproc.popen()` = 带
  `CREATE_NO_WINDOW` + `STARTUPINFO(SW_HIDE)` 的 subprocess；另带 `console_windows()`
  自检函数（数屏幕上可见的控制台窗口）。非 Windows 上就是原样 subprocess
- **全部改道**：`core/detect.py`（python 版本、nvidia-smi）、`core/pipe.py`（起核、taskkill、tasklist）、
  `core/singleton.py`（netstat、tasklist、taskkill）、`ui/image_app.py`（起 webui 后端、netstat、taskkill）、
  启动器打开目录的 `explorer` —— 一律走 winproc。`ui/stop.py` 是命令行小工具，保留原样
- **维修嵌进启动器**：`pythonw launcher/neoforge_launcher.py --repair` = 开窗口并立刻跑
  「诊断 → 自动修 → 复检」，过程与结论全写在窗口日志里，报告落 `work/repair-<时间戳>.md`；
  `NEOFORGE_SILENT=1` 时不弹「现在打开报告吗」的确认框
- **实测**：
  - `tests/test_no_console.py`：静态扫描 `core/ + launcher/ + ui/` 直接调 subprocess **0 处**；
    再用 pythonw 重开自己跑「检测 6 项 1.78 s → 维修预演 1.89 s → 假内核起停 2.22 s（干净）→
    tasklist / netstat」，每 40 ms 采一次屏，**新增控制台窗口 0 个**
  - `tests/test_repair_in_launcher.py`：真起 `pythonw … --repair`，日志实测
    「13 项检查全部通过，可以开工」、报告 `repair-20261031-215059.md`，期间**新增控制台窗口 0 个**
  - 回归：`test_pipe`（就绪 0.4 s、收尸零孤儿）、`test_launcher_smoke`、`test_repair`（13 项全绿）、
    `test_singleton`（互斥体 + 端口守卫）全部通过
- **待办**：桌面上旧的 `NeoForgeSD 维修.cmd` 仍在（删除动作要用户点头）；新版入口是启动器里的
  **维修** 按钮，或桌面快捷方式指向 `pythonw.exe … neoforge_launcher.py --repair`

## 未发布 ｜ 单实例守卫：一个安装一个窗口，一个端口一个核

**实测起因**：2026-10-31 21:19，`nvidia-smi` 里发现 `webui.py --api --nowebui --port 7860`
空转占着 **2375 MiB / 4096 MiB（58%）**、GPU 利用率 0%，而它的父进程已经不在了 ——
典型的「启动器被关掉，内核活成孤儿」。4 GB 卡上再起一份就是互杀。

- **`core/singleton.py`（新）**：Windows 命名互斥体（按 `BASE_DIR` 哈希，`Local\NeoForgeSD-<12位>`）
  + `work/launcher.lock` 诊断锁文件（pid/mutex/启动时间）。第二个启动器不开窗，而是
  `ShowWindow + SetForegroundWindow` 把已有窗口叫到前面，并说清是谁在跑。
  `NEOFORGE_SILENT=1` 时只打印不弹窗（给自动化用）。非 Windows 退到锁文件 + pid 存活判断。
  同一模块还提供 `port_owner()` / `kill_tree()` / `wait_free()`，壳和核共用一套判断。
- **壳**：
  - `WM_DELETE_WINDOW` 接管 —— 关窗时核还在跑就先问「一起停掉？」，停了整棵进程树才退，
    不再留孤儿
  - `on_start` 两条硬规矩：① 本壳已有核在跑 → 拒绝启动（先停，才能起下一个）；
    ② 目标端口已被 listen → 弹窗列出 **pid / 进程名 / 命令行**，「是」= 连子进程一起停掉再启动，
    「否」= 取消（现成的那个点「打开页面」直接用）
  - 选中内核时就探端口：被外人占着会写日志 + 状态栏变黄 + 放开「打开页面」
- **核**：`pipe.KernelRunner.start()` 起进程前先探端口，被占直接 FAILED，
  `last_error` 写明占用者，绝不悄悄起第二份
- **MCP 桥（aigc-bridge 的 `sd_start`）**：原来只靠 HTTP 探活；模型加载中端口已经 listen
  但 `/sdapi` 还没响应，这时会起第二份。现补 TCP 端口检查 —— 被占就拒绝并回报 pid
- **实测**（真跑，全程无 mock）：
  - `tests/test_singleton.py`：同一安装二次抢互斥体返回 `None`；释放后可再抢；另一份安装互不影响；
    能定位并聚焦窗口；端口占用者报 pid 准确（`53635 → pid 15032`）；
    **端口被占时 `KernelRunner.start()` 返回 False，`last_error` = 「端口 53639 已被 pid 11952（python.exe）占用」**
  - `tests/test_single_instance_e2e.py`：真起 pythonw 启动器（pid 15012）→ 第二个进程退出码 0、
    输出「NeoForgeSD 已在运行：pid 15012」、锁仍在第一个手上 → `taskkill` 后互斥体重新可抢
  - `tests/test_bridge_sd_guard.py`：真 MCP stdio JSON-RPC 调用（假监听占 7860），
    `sd_start` 回「Port 7860 is already listening (pid 13244) … Refusing to start a second one」，
    且没动别人的进程
  - 回归：`test_pipe.py`（就绪 0.4 s / 进度 3 条 / 收尸零孤儿）、`test_launcher_smoke.py`、
    `test_repair.py`（13 项全绿）全部通过
- **备份**：`launcher/neoforge_launcher.py.bak-singleton`、`aigc-bridge/server.mjs.bak-singleton`
- **注意**：在跑的旧启动器/旧桥进程还是老代码，关掉重开才生效

## 未发布 ｜ 启动器界面重做（sv-ttk + 品牌橙）

- **主题**：接入 `sv-ttk` 2.6.1（Win11 Fluent 深色），包体只涨 **+0.05 MB**（实测 10.65 → 10.70 MB）；
  `customtkinter`（+7.32 MB）与 `ttkbootstrap`（+8.56 MB）实测后否决
- **品牌橙 `#ff7a2f`**：只用在自绘元素上——启动键、状态点、内核选中行、进度条、日志旁提示。
  sv-ttk 的强调色是预渲染雪碧图（`theme/dark.tcl:12` + `spritesheet_dark.png`），按钮改不动色
- **修掉的六个界面问题**：
  1. 内核下拉 `a1111 · A1111 · NeoForgeSD 前端` 重复念 id —— 左侧改成内核列表 `id + 版本`，全名放详情区
  2. 按钮/进度条/滚动条还是系统亮色 —— 统一由 sv-ttk 接管，主按钮自绘橙
  3. 进度条是条空槽 —— 换成自绘 `Gauge`，带百分比和进度文字，起动时才占位
  4. 日志区空占半窗 —— 日志吃掉剩余空间，日志头右侧显示当前进度阶段
  5. 六项检测挤成一团 —— 独立卡片、等宽对齐、异常项整行变黄/红
  6. 没有品牌色 —— 见上
- **顺手**：Win11 深色标题栏（DWM attr 20/19 + 35）、窗口图标 `assets/neoforge.ico`（螺母造型，7 种尺寸）、
  `pythonw` 双击失败弹窗而不是静默退出
- **实测**：启动中 / 运行中两态截图验证（`D:\AIGC\launcher-ui-lab\ui_starting.png`、`ui_running.png`）；
  桌面快捷方式 `NeoForgeSD.lnk`（目标 `E:\python\pythonw.exe`，工作目录 `E:\NeoForge\app`）双击起窗通过
- **出图位置**：`kernel.json` 新增可选字段 `outputs`（spec v1 兼容），壳显示「路径 · 张数 · 最近一张」并提供「打开目录」。
  a1111 内核声明 `{base}/backend/outputs`，实测 **373 张**，最近 `txt2img-grids\2026-10-19\grid-0002.png`。
  壳不再猜后端布局——目录由核自己说
- **自检**：新增 `core/selfcheck.py`，可脱离界面单跑（`python -m core.selfcheck [--json] [--no-probe]`，
  退出码 0/1/2 = 绿/黄/红）。覆盖 内核登记册 → 每个内核的启动命令/工作目录/端口/出图目录 →
  **假内核真起真停**（就绪 0.4–0.5 s、收尸后端口释放）→ 六项环境侦探；报告落到 `work/selfcheck-<时间戳>.md`。
  界面「自检」按钮逐条写日志并给结论，实测 13 项、1 项需留意（显卡余量）
- **维修（原「自检」+「重新检测」合并）**：新增 `core/repair.py`——诊断 → 自动修能修的 → 复检 → 出报告。
  修的都是幂等安全项：补 `work/` 与出图目录、清占着内核端口的**孤儿内核进程**（正经在跑的不动）、
  删坏掉的 `__pycache__`、从 `.bak` 恢复坏掉的 `kernel.json`、`launcher.log` 超 5 MB 截尾。
  **不碰**模型、不动后端配置、不删用户数据。命令行 `python -m core.repair [--dry-run]`，
  桌面另有独立入口 `NeoForgeSD 维修.cmd`（启动器自己起不来时也能修）。报告落 `work/repair-<时间戳>.md`
- 维修实测：造一个孤儿假内核（pid 9424/14364）→ 预演不动手 → 真修清掉 → 18899 端口释放；
  `tests/test_repair.py` 全流程通过；桌面 `.cmd` 实跑输出正常（UTF-8 + chcp 65001），退出码 1 = 有警告
- `NeoForgeSD.spec` 补 `sv_ttk` hiddenimport 与 `assets` 数据，exe 未重打（桌面入口走源码，改完即生效）
- 按使用习惯调整按钮位置：`打开目录` 改名 **图片**，与 `自检`、`重新检测` 一起挪到窗口底部一排；
  顶部动作行只留 启动 / 停止 / 打开页面；出图那行只显示路径与张数

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
