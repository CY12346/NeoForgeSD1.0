# -*- coding: utf-8 -*-
"""起一个真实的启动器窗口 → 直接停到下载页 → 现场搜一次魔搭，供人工/截图验收。

这不是产品代码，只是验收脚本：
    E:/NeoForge/backend/python/pythonw.exe app/tests/shot_download_page.py
"""
import os
import sys
import time

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
sys.path.insert(0, os.path.join(APP, "launcher"))

import neoforge_launcher as L          # noqa: E402


def main():
    app = L.Launcher()
    app.geometry("1180x760")
    app.after(400, lambda: app.on_open_download())
    if "--nosearch" not in sys.argv:
        def go():
            app.dl_query.set("sd1.5")          # 搜一个稳的关键词
            app.on_fetch_online()
        app.after(1600, go)
    app.after(20000, app.destroy)              # 20 秒后自动关，免得留僵尸窗口
    t0 = time.time()
    app.mainloop()
    print(f"窗口跑了 {time.time() - t0:.1f}s 后退出")


if __name__ == "__main__":
    main()
