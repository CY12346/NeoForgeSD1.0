# -*- coding: utf-8 -*-
"""neoforge-sd15 · 内核运行时包

分层（只允许上层 import 下层，反过来不行）：

    server.py    HTTP 层：路由 ↔ JSON/文件           ← 唯一对外的东西
      ├── engine.py     SD 后端引擎：起停/判活/日志（A1111 方言）
      ├── jobs.py       任务队列 + 工作线程 + 落盘
      ├── payload.py    请求参数 → SD API 载荷
      ├── history.py    历史记录（work/history_image.json）
      └── textfmt.py    提示词中⇄英（包装 vendor/i18n_tags）
            └── sdapi.py   SD HTTP 客户端 + 目录缓存
                  └── config.py   路径 / 端口 / 开关 / 基准目录推导

内核入口是仓库根的 `kernel_main.py`：它只负责环境自检、PIPE 行、把 server 起来。
"""
VERSION = "1.3.0"
