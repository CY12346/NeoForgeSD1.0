# -*- coding: utf-8 -*-
"""打包 + 安装的硬底子：建包 → 校验 → 装 → 篡改/越界/非本格式的包全被拒。

    python tests/test_pack.py

全是真文件真 zip，跑完自己去临时目录看也行（测完自动清）。
"""
import os
import shutil
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import installer, pack  # noqa: E402


def make_tree(base):
    """造一份像模像样的「壳」：该带的带、该跳的跳。"""
    def w(rel, text="x"):
        p = os.path.join(base, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    w("app/launcher/neoforge_launcher.py", "# launcher\nprint('hi')\n")
    w("app/core/pack.py", "# pack\n")
    w("app/core/installer.py", "# installer\n")
    w("app/kernels/fake/kernel.json", '{"spec": 1}\n')
    w("app/README.md", "# readme\n")
    w("app/说明/文档.txt", "中文路径与中文文件名\n")
    w("app/dist/NeoForgeSD.exe", "MZ fake exe")
    # 固定一个时间戳，用来验「装完 mtime 有没有跟着走」
    stamp = 1690000000
    for rel in ("app/说明/文档.txt", "app/core/pack.py"):
        os.utime(os.path.join(base, rel.replace("/", os.sep)), (stamp, stamp))
    # 下面这些都不该进包
    w("app/work/launcher.log", "old log")
    w("app/outputs/a.png", "png")
    w("app/__pycache__/x.pyc", "pyc")
    w("app/build/obj.bin", "bin")
    w("app/_parked/old.py", "old")
    w("app/.git/config", "git")
    return base


def names(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        return zf.namelist()


def check_build(tmp):
    base = make_tree(os.path.join(tmp, "src"))
    out = os.path.join(tmp, "out", "pkg.zip")
    res = pack.build(base, out_path=out, version="9.9.9")
    print(f"建包：{res['files']} 个文件 {res['bytes'] / 1024:.1f} KiB {res['seconds']:.2f}s")
    assert os.path.isfile(out), "包没生成"
    nl = names(out)
    root = "NeoForgeSD-9.9.9"
    print("包内：", nl)
    for need in ("app/launcher/neoforge_launcher.py", "app/core/pack.py",
                 "app/kernels/fake/kernel.json", "NeoForgeSD.exe",
                 "app/说明/文档.txt", "README-先看我.txt", pack.MANIFEST_NAME):
        assert f"{root}/{need}" in nl, f"包里缺 {need}"
    for bad in ("work/", "outputs/", "__pycache__", "/build/", "_parked", ".git"):
        assert not any(bad in n for n in nl), f"不该进包的东西进去了：{bad}"
    assert f"{root}/app/core/" in nl, "没写显式目录条目（别的解压器要靠它认出空目录）"
    assert res["exe"], "有 dist/NeoForgeSD.exe 却没带上"

    ok, detail = pack.verify(out, log=lambda m: print("   ", m))
    assert ok, f"回读校验没过：{detail}"
    print(f"校验：{detail['files']} 个文件全对")
    return out


def check_install(tmp, zip_path):
    insp = installer.inspect(zip_path)
    assert insp["ok"], insp["why"]
    print(f"认包：root={insp['root']} 清单 {len(insp['members'])} 项")
    target = installer.default_target(zip_path, insp["root"])
    print(f"默认落点：{target}")
    assert target.startswith(os.path.dirname(zip_path)), "默认落点不在包旁边"

    res = installer.install(zip_path, target=None, log=lambda m: print("   ", m))
    assert res["ok"], res.get("why")
    assert res["verified"], "安装时没校验"
    for rel in ("app/launcher/neoforge_launcher.py", "app/core/pack.py", "NeoForgeSD.exe",
                "app/说明/文档.txt", "README-先看我.txt"):
        assert os.path.isfile(os.path.join(res["target"], rel.replace("/", os.sep))), f"没落下 {rel}"
    with open(os.path.join(res["target"], "app", "core", "pack.py"), encoding="utf-8") as f:
        assert f.read().strip() == "# pack", "装出来的内容不对"
    # 时间戳要跟着包走（不是装的时候现捏一个）
    src_ts = os.path.getmtime(os.path.join(tmp, "src", "app", "说明", "文档.txt"))
    dst_ts = os.path.getmtime(os.path.join(res["target"], "app", "说明", "文档.txt"))
    assert abs(dst_ts - src_ts) < 3, f"mtime 没带过来：源 {src_ts} 装完 {dst_ts}"
    # 装的账本要落下来，下次更新靠它
    assert os.path.isfile(os.path.join(res["target"], installer.INDEX_NAME)), "没写安装账本"
    print(f"装好：{res['target']} · {res['files']} 文件 {res['seconds']:.2f}s · "
          f"中文名与 mtime 都对")

    # 同名包再拖一次 → 就地更新（账本在，说明是本启动器装的），不再另开 -2
    res2 = installer.install(zip_path, log=lambda m: print("   ", m))
    assert res2["ok"] and res2["target"] == res["target"], "同包重装没走就地更新"
    assert res2["mode"] == "update", f"模式不对：{res2.get('mode')}"
    print(f"同包重装：就地更新到同一目录（新写 {res2['files']}，更新 {res2['updated']}）")

    # 没有账本的陌生目录 → 一律让路，绝不动
    os.remove(os.path.join(res["target"], installer.INDEX_NAME))
    foreign, mode = installer.plan_target(zip_path, insp["root"])
    assert mode == "new" and foreign != res["target"], f"陌生目录没让路：{foreign}"
    assert foreign.endswith("-2"), f"让路后缀不对：{foreign}"
    print(f"陌生目录让路：{os.path.basename(foreign)}")
    return res["target"]


def check_tamper(tmp, zip_path):
    """改一个字节 → sha256 对不上 → 拒绝，且目标目录不落任何文件。"""
    bad = os.path.join(tmp, "tampered.zip")
    with zipfile.ZipFile(zip_path) as zin, zipfile.ZipFile(bad, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename.endswith("app/core/pack.py"):
                data = data + b"\n# evil\n"
            zout.writestr(info, data)
    target = os.path.join(tmp, "tampered-install")
    res = installer.install(bad, target=target, log=lambda m: print("   ", m))
    assert not res["ok"], "篡改包居然装成功了"
    assert "sha256" in res["why"] or "对不上" in res["why"], f"说法不对：{res['why']}"
    left = []
    for dp, _dn, fn in os.walk(target):
        left += fn
    assert not left, f"坏包落了文件：{left}"
    print(f"篡改包被拒：{res['why'][:60]}")


def check_zip_slip(tmp):
    """../ 越界 + 绝对路径 + 非本格式 → 全拒。"""
    slip = os.path.join(tmp, "slip.zip")
    with zipfile.ZipFile(slip, "w") as zf:
        zf.writestr("NeoForgeSD-1.0/neoforge-package.json",
                    '{"schema": "neoforge-package/1", "root": "NeoForgeSD-1.0", "files": []}')
        zf.writestr("NeoForgeSD-1.0/../../evil.txt", "boom")
    insp = installer.inspect(slip)
    assert not insp["ok"], "越界包被放行了"
    assert "越界" in insp["why"] or "清单" in insp["why"], insp["why"]
    print(f"越界包被拒：{insp['why']}")

    plain = os.path.join(tmp, "plain.zip")
    with zipfile.ZipFile(plain, "w") as zf:
        zf.writestr("hello.txt", "not a package")
    insp = installer.inspect(plain)
    assert not insp["ok"] and "neoforge-package.json" in insp["why"], insp["why"]
    print(f"普通 zip 被拒：{insp['why']}")

    fake7z = os.path.join(tmp, "NeoForge-backend-1.2.1.7z.001")
    with open(fake7z, "wb") as f:
        f.write(b"7z\xbc\xaf\x27\x1c not really")
    insp = installer.inspect(fake7z)
    assert not insp["ok"] and ("分卷" in insp["why"] or "zip" in insp["why"]), insp["why"]
    print(f"分卷被拒：{insp['why']}")

    missing = installer.inspect(os.path.join(tmp, "nope.zip"))
    assert not missing["ok"] and "不存在" in missing["why"]
    print(f"不存在的文件被拒：{missing['why']}")


def check_handoff(tmp):
    work = os.path.join(tmp, "work")
    p1 = os.path.join(tmp, "a.zip")
    p2 = os.path.join(tmp, "b.zip")
    installer.handoff([p1, p2], work)
    got = installer.take_handoffs(work)
    assert len(got) == 1, f"递进来的条数不对：{got}"
    fp, paths = got[0]
    assert paths == [os.path.abspath(p1), os.path.abspath(p2)], paths
    assert not os.path.exists(fp), "取走之后没删"
    assert installer.take_handoffs(work) == [], "第二次不该再取到东西"
    print(f"接力箱：收到 {paths}，取完即清")


def main():
    tmp = tempfile.mkdtemp(prefix="neoforge-pack-")
    print(f"临时目录 {tmp}")
    try:
        zip_path = check_build(tmp)
        check_install(tmp, zip_path)
        check_tamper(tmp, zip_path)
        check_zip_slip(tmp)
        check_handoff(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nPACK OK · 建包/校验/安装/拒坏包/接力箱 全过")


if __name__ == "__main__":
    main()
