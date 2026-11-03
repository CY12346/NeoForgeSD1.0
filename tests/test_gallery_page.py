# -*- coding: utf-8 -*-
"""图库页自测：点「图库」切页 → 按时间分组铺格子 → 缩略图真加载 → 返回。

    E:/python/python.exe tests/test_gallery_page.py

真窗口 + 真 mainloop（后台线程的 after 只有在 mainloop 里才合法），断言走 widget 状态。
"""
import os
import sys
import time

os.environ.setdefault("NEOFORGE_SILENT", "1")

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
sys.path.insert(0, os.path.join(APP, "launcher"))

import neoforge_launcher as L      # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  ✓ " if cond else "  ✗ ") + msg, flush=True)
    if not cond:
        FAILS.append(msg)


def main():
    app = L.Launcher()

    def until(cond, timeout, then):
        end = time.time() + timeout

        def tick():
            if cond() or time.time() > end:
                then()
            else:
                app.after(50, tick)
        app.after(50, tick)

    def step_kernels():
        print("[1] 按钮文案 / 默认界面", flush=True)
        check(str(app.btn_outdir["text"]) == "图库", f"底部按钮叫「图库」，实际 {app.btn_outdir['text']!r}")
        check(not app.gallery_visible, "初始在主页")
        check(bool(app.page_main.winfo_manager()), "主界面已 pack")
        check(not app.page_gallery.winfo_manager(), "图库页未 pack")
        if app.ids:
            app.lb.selection_set(0)
            app.refresh_detail()
        m = app.current_manifest()
        print(f"    内核 {m and m['id']} · 出图目录 {m and m.get('outputs')}", flush=True)
        step_switch()

    def step_switch():
        print("[2] 点「图库」切页", flush=True)
        app.btn_outdir.invoke()

        def after_switch():
            check(app.gallery_visible, "gallery_visible = True")
            check(not app.page_main.winfo_manager(), "主界面已收起")
            check(bool(app.page_gallery.winfo_manager()), "图库页已展开")
            check("图库" in app.title(), f"标题带图库：{app.title()!r}")
            print("[3] 等扫描 + 缩略图铺完", flush=True)
            until(lambda: app._gal_cells and len(app._gal_photos) >= len(app._gal_cells) * 0.9,
                  150, step_assert)
        app.after(200, after_switch)

    def step_assert():
        cells = len(app._gal_cells)
        photos = len(app._gal_photos)
        print(f"    格子 {cells} · 缩略图 {photos}", flush=True)
        check(cells > 0, "格子有内容（>0）")
        check(cells <= L.GALLERY_LIMIT, f"不超过上限 {L.GALLERY_LIMIT}")

        groups = [w for w in app.gal_inner.winfo_children()
                  if w.winfo_children() and isinstance(w.winfo_children()[0], L.tk.Label)]
        stamps = [w.winfo_children()[0].cget("text") for w in groups]
        print(f"    日期分组 {len(stamps)} 个：{stamps[:6]}", flush=True)
        check(len(stamps) >= 1, "至少一个日期分组")
        # 组内张数之和 == 格子数（gal_inner 里 head 与 grid 交替出现）
        grids = [w for w in app.gal_inner.winfo_children() if w.grid_slaves()]
        total_in_groups = sum(len(g.grid_slaves()) for g in grids)
        check(total_in_groups == cells, f"分组张数合计 {total_in_groups} == 格子 {cells}")

        ratio = photos / cells if cells else 0
        check(ratio >= 0.9, f"缩略图加载率 {ratio:.0%} ≥ 90%")
        cols = app._gal_cols
        check(cols == app._gal_cols_now(), f"列数按画布宽自适应：{cols} 列（重算仍 {app._gal_cols_now()}）")
        # 每个 grid 里每行的格子数不超过列数
        import collections
        over = []
        for g in grids:
            rows = collections.Counter(c.grid_info()["row"] for c in g.grid_slaves())
            if rows and max(rows.values()) > cols:
                over.append(dict(rows))
        check(not over, f"没有一行超出 {cols} 列：{over[:2]}")
        if app._gal_cells:
            lbl, it = app._gal_cells[0]
            check(bool(lbl.cget("image")), "首个格子挂上了 image（不是空框）")
            print(f"    首格 image={lbl.cget('image')} "
                  f"{app._gal_photos[0].width()}x{app._gal_photos[0].height()} "
                  f"← {os.path.basename(it['path'])}", flush=True)
        print("[4] 返回主界面", flush=True)
        app.on_close_gallery()
        app.after(200, step_back)

    def step_back():
        check(not app.gallery_visible, "gallery_visible = False")
        check(bool(app.page_main.winfo_manager()), "主界面回来了")
        check(not app.page_gallery.winfo_manager(), "图库页收起")
        print("[5] 再进一次（重入不炸）", flush=True)
        app.btn_outdir.invoke()
        until(lambda: len(app._gal_cells) > 0, 60, step_reenter)

    def step_reenter():
        check(app.gallery_visible and len(app._gal_cells) > 0,
              f"第二次进图库照样铺出 {len(app._gal_cells)} 格")
        app.after(100, step_nopil)

    def step_nopil():
        print("[6] 便携包兜底：没有 PIL 时用 Tk 自己读 PNG", flush=True)
        import tempfile
        L._PIL = None                                  # 假装是没装 PIL 的便携包
        L.THUMB_DIR = tempfile.mkdtemp(prefix="nopil_")  # 空缓存目录，逼它走兜底
        app.load_gallery()
        until(lambda: len(app._gal_photos) >= 5, 90, step_nopil_assert)

    def step_nopil_assert():
        app.update()
        cells, photos = len(app._gal_cells), len(app._gal_photos)
        print(f"    格子 {cells} · 兜底缩略图 {photos}", flush=True)
        check(photos > 0, "无 PIL 也能出缩略图")
        if app._gal_photos:
            w = app._gal_photos[0].width()
            h = app._gal_photos[0].height()
            print(f"    兜底尺寸 {w}x{h}", flush=True)
            check(max(w, h) <= 160, f"兜底缩略图长边 {max(w, h)} ≤ 156 的整数倍范围内")
        app.after(100, finish)

    def finish():
        app.destroy()

    def kick():
        until(lambda: bool(app.ids), 20, step_kernels)

    app.after(200, kick)
    app.mainloop()

    print(flush=True)
    if FAILS:
        print(f"失败 {len(FAILS)} 项：")
        for f in FAILS:
            print("  -", f)
        return 1
    print("图库页自测全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
