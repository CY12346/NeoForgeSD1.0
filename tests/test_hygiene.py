# -*- coding: utf-8 -*-
"""hygiene 守门测试：造污染 -> 扫出问题 -> 归位 -> 复扫干净 -> 幂等。

全部在临时目录里做，不碰真实 work/。
跑法：python tests/test_hygiene.py
"""
import os
import sys
import tempfile
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core import hygiene  # noqa: E402


def main():
    tmp = tempfile.mkdtemp(prefix="hyg_")
    try:
        work = os.path.join(tmp, "work")
        os.makedirs(work)
        pollution = {
            "aaa_00.png": b"\x89PNG" + b"0" * 500,
            "bbb_01.png": b"\x89PNG" + b"0" * 500,
            "clip.mp4": b"0" * 1000,
            "install-20261117-101010.md": b"# install",
            "selfcheck-20261116-090000.md": b"# selfcheck",
            "repair-20261117-100000.md": b"# repair",
            "sd_server.log": b"log line\n",
            "notes.md": b"# user notes",           # 用户自己的笔记，不该动
            "history_image.json": b"[]",           # 运行时文件，不该动
            "launcher.lock": b"1234",              # 运行时文件，不该动
        }
        for n, b in pollution.items():
            with open(os.path.join(work, n), "wb") as f:
                f.write(b)

        # 1) 扫得出问题
        issues, stats = hygiene.scan_layout(tmp)
        assert stats == {"images": 3, "logs": 1, "reports": 3, "skipped": []}, stats
        assert any(i["level"] == "yellow" for i in issues), "污染没被报出来"

        # 2) 预演不动手
        a = hygiene.fix_layout(tmp, dry=True)
        assert a["ok"] and "预演" in a["detail"], a
        assert len([n for n in os.listdir(work) if os.path.isfile(os.path.join(work, n))]) == 10

        # 3) 真归位
        a = hygiene.fix_layout(tmp)
        assert a["ok"], a
        assert sorted(os.listdir(os.path.join(work, "images"))) == ["aaa_00.png", "bbb_01.png", "clip.mp4"]
        assert len(os.listdir(os.path.join(work, "reports"))) == 3
        assert os.listdir(os.path.join(work, "logs")) == ["sd_server.log"]
        rest = set(os.listdir(work))
        assert "notes.md" in rest and "history_image.json" in rest and "launcher.lock" in rest, rest

        # 4) 复扫干净
        issues2, _ = hygiene.scan_layout(tmp)
        assert all(i["level"] == "green" for i in issues2), issues2

        # 5) 幂等
        a2 = hygiene.fix_layout(tmp)
        assert a2["ok"] and "没有需要归位" in a2["detail"], a2

        print("✅ test_hygiene 全过：报污染 3 项 -> 预演不动 -> 归位 7 个 -> 复扫全绿 -> 幂等")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
