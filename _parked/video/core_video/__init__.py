# -*- coding: utf-8 -*-
"""视频支线。motion = 立绘木偶形变场（零显存），quality = 连贯性体检，
traj = 轨迹驱动（从参考片抽事件再驱动木偶）。

子模块不在这里急切导入：只用出图功能时不该拉起 numpy/PIL。
"""
__all__ = ["motion", "quality", "traj"]
