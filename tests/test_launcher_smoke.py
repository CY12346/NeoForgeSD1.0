# -*- coding: utf-8 -*-
"""启动器冒烟：真建一次窗口（建完立刻隐藏并销毁），确认 1.3.0 的壳能起来。

不点按钮、不启动内核，只验证：路径推导对、内核登记册能读、侦探能跑、控件能建。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import launcher.neoforge_launcher as L  # noqa: E402


def main():
    print(f"VERSION={L.VERSION}")
    print(f"HERE={L.HERE}")
    print(f"BASE_DIR={L.BASE_DIR}")
    assert os.path.isdir(L.BASE_DIR), "BASE_DIR 不存在"
    assert L.VERSION.startswith("1.3"), "版本号没升到 1.3"

    from core import detect
    manifests, bad = L.discover_all()
    ids = list(manifests)
    print(f"登记内核={ids} 坏件={bad}")
    assert ids, "壳读不到任何内核"

    app = L.Launcher()
    app.withdraw()
    app.update_idletasks()
    app.update()
    app.reload_kernels()
    app.update()
    print(f"窗口已建：title={app.title()} 内核下拉={app.cmb['values']}")
    assert app.cmb["values"], "下拉是空的"

    m = app.current_manifest()
    print(f"当前内核={m['id'] if m else None} 端口={m['port'] if m else None}")
    assert m, "选不出内核"

    items = detect.run_checks(L.BASE_DIR)
    level, summary = detect.verdict(items)
    print(f"侦探结论={level}：{summary[:100]}")
    for it in items:
        print(f"  [{it['level']:6s}] {it['label']:10s} {it['detail'][:70]}")

    app.destroy()
    print("SMOKE OK")


if __name__ == "__main__":
    main()
