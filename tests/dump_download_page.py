# -*- coding: utf-8 -*-
"""不开窗验证下载页：跑一遍 refresh_downloads，把 Treeview 的行打出来。

    E:/NeoForge/backend/python/python.exe tests/dump_download_page.py
"""
import os
import sys

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
sys.path.insert(0, os.path.join(APP, "launcher"))

import tkinter as tk                       # noqa: E402

import neoforge_launcher as L             # noqa: E402


def main():
    app = L.Launcher()
    app.withdraw()                         # 别抢用户的焦点
    app.update_idletasks()
    app.on_open_download()
    app.update_idletasks()

    cols = app.dl_tree["columns"]
    rows = app.dl_tree.get_children()
    print("列：", [app.dl_tree.heading(c)["text"] for c in cols])
    print(f"共 {len(rows)} 行\n")
    print(f"{'类型':<12} {'名称':<34} {'大小':>9} {'热度':>8} {'来源':<5} 状态")
    print("-" * 88)
    for iid in rows[:26]:
        v = [app.dl_tree.set(iid, c) for c in cols]
        print(f"{v[0]:<12} {v[1][:33]:<34} {v[2]:>9} {v[3]:>8} {v[4]:<5} {v[5]}")
    print("...")
    print("\n各类别前 3（按热度降序）：")
    by_id = {it["id"]: it for it in app._dl_items}
    for kind, label in (("extension", "插件"), ("ui", "UI 本体")):
        got = [app.dl_tree.set(i, "name") + "  " + app.dl_tree.set(i, "hot")
               for i in rows if (by_id.get(i) or {}).get("kind") == kind][:3]
        print(f"  {label}: " + " | ".join(got))
    app.destroy()


if __name__ == "__main__":
    main()
