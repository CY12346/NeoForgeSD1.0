# -*- coding: utf-8 -*-
"""端到端：真窗口 + 真 WM_DROPFILES → 启动器自动装包。

不叫人拖鼠标：自己拼一个真的 HDROP（GlobalAlloc 出来），SendMessage(WM_DROPFILES)
打到启动器窗口上 —— 走的是系统那条路，跟拿鼠标拖一样。

    python tests/test_pack_drop.py

覆盖：
  1. 拖进 zip → 自动校验 → 装到包旁边同名目录
  2. 拖进一个 txt → 不崩、不装、日志说清为什么
  3. 另一个进程把包递进 work/drop-inbox/（= 拖到 exe 图标上那份的接力）→ 也会装

做法：整个流程跑在真的 mainloop 里（Tk 的后台线程只能往「正在跑」的主循环里投消息），
      每一步用 after() 轮询条件 + 超时兜底，一步不过就记下来销毁窗口。
"""
import os
import shutil
import sys
import tempfile
import time
from glob import glob

os.environ["NEOFORGE_SILENT"] = "1"          # 只在日志里说话，不弹任何窗

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import launcher.neoforge_launcher as L   # noqa: E402
from core import droptarget, installer, pack  # noqa: E402


def make_tree(base):
    def w(rel, text="x"):
        p = os.path.join(base, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
    w("app/launcher/neoforge_launcher.py", "# stub\n")
    w("app/core/pack.py", "# pack\n")
    w("app/kernels/fake/kernel.json", '{"spec": 1}\n')
    return base


def main():
    tmp = tempfile.mkdtemp(prefix="neoforge-drop-")
    zip_path = os.path.join(tmp, "NeoForgeSD-9.9.9-test.zip")
    print(f"临时目录 {tmp}")
    res = pack.build(make_tree(os.path.join(tmp, "src")), out_path=zip_path, version="9.9.9",
                     log=lambda m: print("   ", m))
    print(f"包：{res['path']} {res['files']} 文件 {res['bytes']} B")

    app = L.Launcher()
    app.geometry("+2600+2600")          # 挪到屏幕外，别在人家桌面闪一下
    state = {"error": None, "checks": []}

    def log_text():
        return app.log.get("1.0", "end")

    def fail(msg):
        if not state["error"]:
            state["error"] = msg
        try:
            app.after(30, app.destroy)
        except Exception:
            pass

    def need(cond, label):
        """cond 成立就把 label 记进成绩单，不成立就把 label 当失败点记下来。"""
        if cond:
            state["checks"].append(label)
        else:
            fail(f"✗ {label}\n日志尾部：\n{log_text()[-900:]}")

    def wait(cond, then, timeout, what):
        deadline = time.time() + timeout

        def poll():
            if state["error"]:
                return
            try:
                hit = cond()
            except Exception as e:
                fail(f"{what}：判断条件自己出错了 {e}")
                return
            if hit:
                then()
                return
            if time.time() > deadline:
                fail(f"超时 {timeout:.0f}s：{what}｜装好计数={log_text().count('✓ 装好了')}"
                     f"｜窗口={app.winfo_exists()}｜末行={log_text().strip().splitlines()[-1:]!r}")
                return
            app.after(60, poll)

        app.after(60, poll)

    target = os.path.join(tmp, "NeoForgeSD-9.9.9")

    # ---- 3（最后一步先写，串起来用）接力箱
    def step3_handoff():
        box_zip = os.path.join(tmp, "NeoForgeSD-9.9.9-again.zip")
        shutil.copy2(zip_path, box_zip)
        installer.handoff([box_zip], L.WORK_DIR)
        # 同一个 root 再装一次 → 账本在，就地更新到同一目录（不再另开 -2）
        wait(lambda: log_text().count("✓ 装好了") >= 2, step3_done, 25, "接力箱里的包被装好")

    def step3_done():
        need("收到递进来的 1 个包" in log_text(), "接力箱接单记录写进了日志")
        need("就地更新" in log_text(), "第二次装走了就地更新（账本被认出来）")
        need(os.path.isfile(os.path.join(target, "app", "core", "pack.py")), "就地更新后文件还在")
        need(os.path.isfile(os.path.join(target, installer.INDEX_NAME)), "安装账本落在目标目录里")
        # 窗口还活着、内核登记册还在
        need(app.lb.size() > 0, f"窗口仍可交互：内核 {app.lb.size()} 个")
        app.after(50, app.destroy)

    # ---- 2 拖进一个不是包的文件
    def step2_check():
        need("认不出是安装包" in log_text(), "非包文件被点名拒绝（不装、也不崩）")
        need(os.path.isdir(target), "非包文件没把已有安装弄没")
        step3_handoff()

    def step2_drop():
        junk = os.path.join(tmp, "随手一个.txt")
        with open(junk, "w", encoding="utf-8") as f:
            f.write("not a package")
        droptarget.send_drop(app.drop.hwnd, droptarget.make_hdrop([junk]))
        wait(lambda: "认不出是安装包" in log_text(), step2_check, 15, "非包文件被识别")

    # ---- 1 拖进真包
    def step1_check():
        need(os.path.isfile(os.path.join(target, "app", "core", "pack.py")), "拖进去自动装好")
        need("✓ 装好了" in log_text(), "日志里有安装结论")
        need("sha256 全对" in log_text(), "日志里有校验记录")
        need(app.drop.dropped == 1, "WM_DROPFILES 收到 1 次")
        step2_drop()

    def step1_drop():
        droptarget.send_drop(app.drop.hwnd, droptarget.make_hdrop([zip_path]))
        wait(lambda: os.path.isfile(os.path.join(target, "app", "core", "pack.py")),
             step1_check, 30, "拖进去的包被装好")

    def start():
        need(getattr(app, "drop", None) is not None, "拖放挂上了（窗口过程已换成自己的）")
        if state["error"]:
            return
        state["checks"].append(f"拖放就位 hwnd={app.drop.hwnd} 旧过程={app.drop._old}")
        step0_button()

    # ---- 0 「打包」按钮（真打一次这份仓库的壳，验完把包删掉不占地）
    def step0_button():
        before = set(glob(os.path.join(L.PACK_DIR, "*.zip")))
        state["before_pack"] = before

        def cond():
            return (not getattr(app, "_packing", False)
                    and bool(set(glob(os.path.join(L.PACK_DIR, "*.zip"))) - before))

        app.on_pack()
        wait(cond, step0_check, 180, "点「打包」出包")

    def step0_check():
        new = set(glob(os.path.join(L.PACK_DIR, "*.zip"))) - set(state["before_pack"])
        need(bool(new), "点「打包」真出了 zip")
        if not new:
            return
        built = sorted(new)[-1]
        ok, detail = pack.verify(built, log=lambda _m: None)
        need(ok, f"包自校验通过（{os.path.getsize(built) / 1048576:.2f} MiB）")
        need("打包完成" in log_text(), "日志里有打包结论")
        state["checks"].append(f"包：{built}")
        try:
            os.remove(built)                 # 测试产物不留在 work/pack 里
        except OSError:
            pass
        step1_drop()

    app.after(300, start)            # 200ms 那条 _enable_drop 先跑
    app.mainloop()

    shutil.rmtree(tmp, ignore_errors=True)
    for line in state["checks"]:
        print(line)
    if state["error"]:
        raise AssertionError(state["error"])
    print("\nDROP OK · 拖进来就装、非包不装、接力箱照单，全程无弹窗无黑框")


if __name__ == "__main__":
    main()
