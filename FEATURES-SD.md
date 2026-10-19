# SD 功能清单 → NeoForgeSD 分组（提炼依据：本机 Forge 的 `ui-config.json` + `/sdapi/v1/*` 实测）

> 结论先行：这套 SD 装了很多东西（Deforum / SuperMerger / EasyPhoto / Inpaint Anything / Tagger…），
> 但**只有通过 HTTP API 能做到的**才值得搬进 NeoForgeSD；其余（训练、合并模型、插件管理）留在 SD 里做。

## 一、SD 里有哪些页签（ui-config 实测的顶层 tab）

| tab | 控件的数量级 | 是什么 |
|---|---|---|
| `txt2img` | 869 | 文生图 |
| `img2img` | 908 | 图生图 / 局部重绘(inpaint) / 批量 |
| `extras` | 53 | 放大、批量放大、面部修复(GFPGAN/CodeFormer) |
| `pnginfo` | 4 | 从 PNG 读参数 |
| `customscript` | 1565 | 各种脚本（ADetailer / ControlNet / Dynamic Thresholding / LoRA Block Weight / Refiner / Reactor / Tiled Diffusion…） |
| `supermerger` | 445 | LoRA/权重合并 |
| `EasyPhoto_tabs` | 367 | 数字人 |
| `inpaint_anything` | 130 | SAM 涂抹重绘 |
| `train` | 85 | 训练 |
| `modelmerger` / `model_converter` / `checkpoint_toolkit` | 87 | 合并 / 转换 / 拆分 |
| `tagger` | 47 | 反推提示词（wd14） |
| `system_info` | 52 | 环境信息 |
| `infinite-image-browsing` | 5 | 图库浏览器 |
| `threedopenpose` | 6 | 3D 姿势 |

## 二、能不能搬（按 API 实测）

| SD 功能 | 对应 API | 能不能 | 放哪组 |
|---|---|---|---|
| 文生图 | `POST /sdapi/v1/txt2img` | ✅ 已有 | **创作** |
| 图生图 | `POST /sdapi/v1/img2img` | ✅ 已有 | **创作** |
| 局部重绘（蒙版） | `img2img` + `mask` / `inpainting_fill` / `inpaint_full_res` | ✅ **已实现**（蒙版笔刷，实测蒙版外差 0.07） | **创作** |
| 高清修复 Hires.fix | `txt2img` + `enable_hr*` | ✅ 已有 | **创作·高级** |
| ADetailer 面部/手部修复 | `alwayson_scripts.adetailer` | ✅ 已有（face 实测；hand 需联网下模型） | **创作·高级** |
| LoRA（含权重） | prompt `<lora:名:0.8>` + `/sdapi/v1/loras` | ✅ 已有 | **创作·高级** |
| 采样器 / 调度器 / 步数 / CFG / seed / 张数 | `txt2img` 参数 | ✅ 已有 | **创作** |
| 底模切换 | `/sdapi/v1/options` + `/sd-models` | ✅ 已有 | **模型** |
| VAE 切换 | `/sdapi/v1/sd-vae` + `options.sd_vae` | ✅ **已实现**（本机 6 个） | **模型** |
| CLIP skip | `options.CLIP_stop_at_last_layers` | ✅ 已有 | **模型** |
| Extras 放大 | `/sdapi/v1/extra-single-image` | ✅ 已有 | **后期** |
| GFPGAN / CodeFormer 修脸 | `extra-single-image` 的 `gfpgan_visibility` / `codeformer_visibility` | ✅ **已实现** | **后期** |
| PNG 信息（读参数） | `POST /sdapi/v1/png-info` | ✅ **已实现**（读回并可一键回填表单） | **资产** |
| 反推提示词（Tagger / CLIP interrogate） | `POST /sdapi/v1/interrogate`（`interrogate` 模型需本机有） | ⚠️ 未验证 | **资产（二期）** |
| ControlNet | `/controlnet/*` | ⚠️ 可做，但每张图要配 unit + 预处理器，工作量大 | 二期 |
| XY 图表 / Prompt matrix | 脚本 | ❌ 不做（低频，UI 成本高） | — |
| Deforum / EasyPhoto | — | ❌ 不做 | — |
| 训练 / 合并模型 / 转换 / 拆分 | 无对应 API | ❌ 不做（留在 SD / 工具脚本） | — |
| 插件管理 / 设置全量 | 部分 `options` | ❌ 不做（危险且低频） | — |

## 三、NeoForgeSD 新分组（前端按这个改）

```
① 创作   文生图 · 图生图 · 局部重绘(蒙版笔刷)
         底部「高级」：ADetailer 面部修复 · LoRA · 高清修复
         右侧：画布（结果）
② 后期   放大（extras，含 GFPGAN/CodeFormer 修脸）
③ 模型   底模 · VAE · CLIP skip · 采样器 · 调度器
④ 资产   历史（点图回填） · PNG 信息（读参数/回填表单） · 打开产物目录
```

分组原则：**影响「生成」的放创作；影响「引擎」的放模型；影响「已生成的图」的放后期；影响「资产」的放资产。**
