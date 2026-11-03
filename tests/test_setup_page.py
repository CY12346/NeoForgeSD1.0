# -*- coding: utf-8 -*-
"""启动器 0.2：首启引导页（选 UI + 优劣）与下载页，真窗口里跑。

    python tests/test_setup_page.py

验证四件事：
  1. 没做过选择、也没有后端 python → 打开就落在引导页
  2. 两张卡片上 A1111 / ComfyUI 的 优点·缺点 都在（不是摆设）
  3. 选了 A1111 → 写 setup.json，跳下载页，列表里有 5 类、并已预选
  4. 拖整合包装成功（_install_done ok）→ setup.json 变 package，再开不再问
"""
import json
import os
import shutil
import sys
import tempfile
import time

os.environ["NEOFORGE_SILENT"] = "1"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import launcher.neoforge_launcher as L   # noqa: E402


def texts(widget, acc=None):
    acc = acc if acc is not None else []
    for w in widget.winfo_children():
        try:
            t = w.cget("text")
            if t:
                acc.append(str(t))
        except Exception:
            pass
        texts(w, acc)
    return acc


def main():
    backup = None
    if os.path.isfile(L.SETUP_FILE):
        backup = L.SETUP_FILE + ".testbak"
        shutil.copy2(L.SETUP_FILE, backup)
        os.remove(L.SETUP_FILE)

    real_has_backend = L.has_backend
    L.has_backend = lambda *a, **k: False        # 假装这台机器是新的
    tmp = tempfile.mkdtemp(prefix="neoforge-setup-")
    state = {"error": None, "checks": []}

    app = L.Launcher()
    app.geometry("+2600+2600")

    def need(cond, label):
        if cond:
            state["checks"].append(label)
        else:
            state["error"] = state["error"] or f"✗ {label}"
            try:
                app.after(20, app.destroy)
            except Exception:
                pass

    def wait(cond, then, timeout, what):
        deadline = time.time() + timeout

        def poll():
            if state["error"]:
                return
            if cond():
                then()
                return
            if time.time() > deadline:
                need(False, f"超时 {timeout:.0f}s：{what}")
                return
            app.after(60, poll)

        app.after(60, poll)

    def step1():
        # 1. 首启应该落在引导页
        need(app.current_page is app.page_setup, "首启落在引导页")
        need("第一次用" in app.title(), f"标题带「第一次用」：{app.title()}")
        page_text = "\n".join(texts(app.page_setup))
        need("WebUI（Forge）" in page_text, "卡片一：WebUI（Forge）")
        need("ComfyUI" in page_text, "卡片二：ComfyUI")
        need("优点" in page_text and "缺点" in page_text, "两张卡片都写了 优点/缺点")
        need("ADetailer" in page_text, "Forge 优点写到了 ADetailer（插件兼容）")
        need("ADetailer 那种一键修脸" in page_text or "没有 ADetailer" in page_text,
             "ComfyUI 缺点也点了名（不搞一边倒宣传）")
        need("推荐" in page_text, "标了推荐项")
        step2()

    def step2():
        app.on_choose_ui("forge")
        data = json.load(open(L.SETUP_FILE, encoding="utf-8")) if os.path.isfile(L.SETUP_FILE) else {}
        need(data.get("ui") == "forge", f"选择写进 setup.json：{data.get('ui')}")
        need(app.current_page is app.page_download, "选完跳到下载页")
        app.update_idletasks()
        rows = app.dl_tree.get_children()
        kinds = {it.get("kind") for it in app._dl_items}
        subs = {app.dl_tree.set(i, "type") for i in rows}
        need(len(rows) >= 10, f"下载页列出 {len(rows)} 项")
        need({"model-big", "model-small", "extension", "ui"} <= kinds,
             f"四类都在：{sorted(k for k in kinds if k)}")
        need(all(subs), f"每一行都标了类型：{sorted(subs)[:6]}")
        need(len(app.dl_tree.selection()) >= 3, f"已预选 {len(app.dl_tree.selection())} 项")
        step3()

    def step3():
        # 模拟「拖整合包装成功」：不再问引导
        res = {"ok": True, "target": tmp, "files": 3, "bytes": 10, "seconds": 0.1,
               "zip": "NeoForgeSD-1.3.0.zip", "manifest": {"version": "1.3.0"}}
        app._install_done(res)
        data = json.load(open(L.SETUP_FILE, encoding="utf-8"))
        need(data.get("ui") == "package", f"装包后 setup.json → {data.get('ui')}")
        need(data.get("version") == "1.3.0", "顺手记下了包的版本")
        app._show_page(app.page_main)
        app.maybe_first_run()                     # 再调一次首启逻辑
        need(app.current_page is app.page_main, "装过包之后不再弹引导页")
        app.after(60, app.destroy)

    app.after(700, step1)
    app.mainloop()

    L.has_backend = real_has_backend
    shutil.rmtree(tmp, ignore_errors=True)
    if backup and os.path.isfile(backup):
        shutil.move(backup, L.SETUP_FILE)        # 还原这台机器原来的状态
    elif os.path.isfile(L.SETUP_FILE):
        os.remove(L.SETUP_FILE)

    for line in state["checks"]:
        print("  ✓", line)
    if state["error"]:
        raise AssertionError(state["error"])
    print("\nSETUP-PAGE OK · 首启引导 / 优劣齐全 / 选完预选下载 / 装包后不再问")


if __name__ == "__main__":
    main()
