# -*- coding: utf-8 -*-
"""内核入口包的公共说明。

分层（单向依赖，下层不 import 上层）：
    config   ← 路径 / 端口 / 模型怎么摆（不看 HTTP）
    pipeline ← config；一次出图的全部动作（纯推理，不碰端口、不碰文件）
    server   ← config / pipeline；唯一的 HTTP 入口

跟 neoforge-sd15 的区别：这里没有 engine/sdapi/payload —— SANA 不走 A1111，
推理直接在进程里跑，参数就是它自己的那几个。
"""
