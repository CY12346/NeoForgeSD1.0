# -*- coding: utf-8 -*-
"""安装器的硬骨头：GBK 文件名、三态合并（用户改过/删过）、空包、已失效文件不删。

    python tests/test_install_merge.py

这些都是从 HMCL 的 Unzipper / CompressingUtils / ModpackInstallTask 学来的规矩：
  · 文件名编码要猜（老工具不写 UTF-8 标记，中文名会变乱码路径）
  · 覆盖已有安装走三态合并：改过的不动、删掉的不复活、其余照更新
  · 空包（只有清单没有文件）当失败，不是当成功
"""
import os
import shutil
import struct
import sys
import tempfile
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import installer, pack  # noqa: E402


# ---------------------------------------------------------------- 造老式 zip
def write_legacy_zip(path, entries, encoding="gbk"):
    """手搓一个 PKZIP 2.0 包：文件名按指定编码写原始字节、不带 UTF-8 标记。

    模拟「用 7-Zip / 好压 之类的中文环境工具重打了一次包」—— 正是 HMCL 那段
    findSuitableEncoding 要救的场面。
    """
    local, central, offset = [], [], 0
    for name, data in entries:
        raw = name.encode(encoding)
        crc = zlib.crc32(data) & 0xFFFFFFFF
        head = struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0, 0, 0, 0x21,
                           crc, len(data), len(data), len(raw), 0)
        local.append(head + raw + data)
        central.append(struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 0, 0,
                                   0, 0x21, crc, len(data), len(data), len(raw),
                                   0, 0, 0, 0, 0, offset) + raw)
        offset += len(head) + len(raw) + len(data)
    body = b"".join(local)
    cd = b"".join(central)
    eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(entries), len(entries),
                       len(cd), len(body), 0)
    with open(path, "wb") as f:
        f.write(body + cd + eocd)


def manifest_for(root, entries):
    """按给定 (路径, 内容) 生成清单 json —— 路径用正常 UTF-8 中文。"""
    import hashlib
    import json
    files = [{"path": p, "size": len(d), "sha256": hashlib.sha256(d).hexdigest()}
             for p, d in entries]
    return json.dumps({"schema": installer.SCHEMA, "app": "NeoForgeSD", "version": "9.9.9",
                       "kind": "app", "created": "2026-10-31 22:00:00", "root": root,
                       "count": len(files), "bytes": sum(f["size"] for f in files),
                       "files": files}, ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------- 用例
def check_legacy_encoding(tmp):
    root = "NeoForgeSD-9.9.9"
    payload = [("app/说明.txt", "中文内容\n".encode("utf-8")),
               ("app/core/pack.py", b"# pack\n")]
    zp = os.path.join(tmp, "legacy-gbk.zip")
    write_legacy_zip(zp, [(f"{root}/neoforge-package.json", manifest_for(root, payload))]
                     + [(f"{root}/{p}", d) for p, d in payload], encoding="gbk")

    insp = installer.inspect(zp)
    assert insp["ok"], insp["why"]
    print(f"认包：名字编码猜成 {insp['name_encoding']}，条目 {sorted(insp['members'])}")
    assert insp["name_encoding"].lower().replace("-", "") in ("gbk", "gb18030", "cp936"), \
        f"没猜出 GBK：{insp['name_encoding']}"
    assert "app/说明.txt" in insp["members"], "GBK 中文名没解出来"

    target = os.path.join(tmp, "gbk-install")
    res = installer.install(zp, target=target, log=lambda m: print("   ", m))
    assert res["ok"], res["why"]
    got = os.path.join(target, "app", "说明.txt")
    assert os.path.isfile(got), f"没落到中文路径：{os.listdir(os.path.join(target, 'app'))}"
    with open(got, encoding="utf-8") as f:
        assert f.read() == "中文内容\n"
    print(f"GBK 名字包正常装到：{got}")


def check_empty(tmp):
    root = "NeoForgeSD-9.9.9"
    zp = os.path.join(tmp, "empty.zip")
    import zipfile
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr(f"{root}/{installer.MANIFEST_NAME}", manifest_for(root, []))
    insp = installer.inspect(zp)
    assert not insp["ok"] and "一个文件都没有" in insp["why"], insp
    print(f"空包被拒：{insp['why']}")


def make_tree(base, files):
    for rel, text in files.items():
        p = os.path.join(base, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
    return base


def check_three_way(tmp):
    """改过的不动、删掉的不复活、其余更新、失效的不删。"""
    work = os.path.join(tmp, "merge")
    v1 = make_tree(os.path.join(work, "v1"), {
        "app/launcher/neoforge_launcher.py": "# stub\n",
        "app/a.txt": "A1", "app/b.txt": "B1", "app/c.txt": "C1",
    })
    v2 = make_tree(os.path.join(work, "v2"), {
        "app/launcher/neoforge_launcher.py": "# stub\n",
        "app/a.txt": "A1",       # 没变（但用户删了 → 不复活）
        "app/b.txt": "B2",       # 变了（但用户改过 → 保留用户的）
        "app/c.txt": "C2",       # 变了（用户没动 → 更新）
        "app/d.txt": "D2",       # 新增
    })
    z1 = os.path.join(work, "p1.zip")
    z2 = os.path.join(work, "p2.zip")
    pack.build(v1, out_path=z1, version="9.9.9", log=lambda _m: None)
    pack.build(v2, out_path=z2, version="9.9.9", log=lambda _m: None)

    r1 = installer.install(z1, log=lambda m: print("   v1", m))
    assert r1["ok"] and r1["mode"] == "new", r1
    tgt = r1["target"]
    print(f"v1 新装到 {tgt}")

    # 用户动手：改 b、删 a
    with open(os.path.join(tgt, "app", "b.txt"), "w", encoding="utf-8") as f:
        f.write("B-user")
    os.remove(os.path.join(tgt, "app", "a.txt"))

    z2b = os.path.join(work, "p2-same-root.zip")
    shutil.copy2(z2, z2b)
    plan, mode = installer.plan_target(z2b, "NeoForgeSD-9.9.9")
    assert mode == "update" and plan == tgt, f"没认出该就地更新：{plan} {mode}"

    r2 = installer.install(z2b, log=lambda m: print("   v2", m))
    assert r2["ok"] and r2["mode"] == "update", r2

    def read(rel):
        p = os.path.join(tgt, rel.replace("/", os.sep))
        if not os.path.isfile(p):
            return None
        with open(p, encoding="utf-8") as f:
            return f.read()

    assert read("app/a.txt") is None, "用户删掉的 a.txt 又回来了"
    assert read("app/b.txt") == "B-user", f"用户改过的 b.txt 被覆盖了：{read('app/b.txt')}"
    assert read("app/c.txt") == "C2", f"没动过的 c.txt 没更新：{read('app/c.txt')}"
    assert read("app/d.txt") == "D2", "新增的 d.txt 没写进来"
    assert r2["kept"] == 1 and r2["skipped_deleted"] == 1, f"计数不对：{r2['kept']}/{r2['skipped_deleted']}"
    print(f"三态合并：新写 {r2['files']} · 更新 {r2['updated']} · 保留 {r2['kept']} · "
          f"未复活 {r2['skipped_deleted']}")

    # 第三包：去掉 c.txt → 只列进「已失效」，文件不许删
    v3 = make_tree(os.path.join(work, "v3"), {
        "app/launcher/neoforge_launcher.py": "# stub\n",
        "app/b.txt": "B3", "app/d.txt": "D3",
    })
    z3 = os.path.join(work, "p3.zip")
    pack.build(v3, out_path=z3, version="9.9.9", log=lambda _m: None)
    r3 = installer.install(z3, log=lambda m: print("   v3", m))
    assert r3["ok"] and r3["mode"] == "update", r3
    assert "app/c.txt" in r3["stale"], f"没记下已失效文件：{r3['stale']}"
    assert read("app/c.txt") == "C2", "失效文件被删了（不该删用户的东西）"
    print(f"v3：已失效 {r3['stale']}，文件仍在（不删）")

    # 账本里的 sha256 要跟磁盘一致（下次更新靠它判断用户动没动过）
    idx = installer.installed_index(tgt)
    assert idx and idx["version"] == "9.9.9", "账本没更新"
    assert idx["files"]["app/c.txt"]["sha256"], "账本里没有 c.txt 的记录"
    print(f"账本：{len(idx['files'])} 条记录，version={idx['version']}")


def main():
    tmp = tempfile.mkdtemp(prefix="neoforge-merge-")
    print(f"临时目录 {tmp}")
    try:
        check_legacy_encoding(tmp)
        check_empty(tmp)
        check_three_way(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nINSTALL-MERGE OK · GBK 名字 / 空包拒绝 / 三态合并 / 失效不删 全过")


if __name__ == "__main__":
    main()
