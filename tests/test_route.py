# -*- coding: utf-8 -*-
"""route 守门测试：真契约能载入、step.entry 指向真实文件、坏契约逐条被拦住。

跑法：python tests/test_route.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core import route, selfcheck  # noqa: E402

CASES = [
    ("缺 name", lambda r: r.pop("name")),
    ("spec 太新", lambda r: r.__setitem__("spec", 9)),
    ("槽位缺 type", lambda r: r["inputs"][0].pop("type")),
    ("type 不在白名单", lambda r: r["inputs"][0].__setitem__("type", "hologram")),
    ("steps 为空", lambda r: r.__setitem__("steps", [])),
    ("parked 但没有 path", lambda r: r["impl"].pop("path")),
    ("enum 没 options", lambda r: r["inputs"][1].pop("options")),
    ("vram 是负数", lambda r: r["needs"].__setitem__("vram_gb", -1)),
    ("outputs 为空", lambda r: r.__setitem__("outputs", [])),
]


def main():
    base = selfcheck.base_of(ROOT)
    routes, bad = [], []
    for root in (base, os.path.join(base, "app")):
        rs, bs = route.discover(root, base_dir=base)
        routes += rs
        bad += bs
    assert not bad, f"不该有坏契约：{bad}"
    assert routes, "一条路线都没有——routes/<id>/route.json 没建或没被扫到"

    # step.entry 一旦写了绝对路径，就必须真的存在（契约不许撒谎）
    for r in routes:
        for st in r["steps"]:
            entry = st.get("entry", "")
            if not entry or not os.path.isabs(entry):
                continue
            assert os.path.exists(entry), f"{r['id']}/{st['tool']} 的 entry 不存在：{entry}"

    # 至少要有一条零显存路线——它和吃显存的路线不是备选关系
    assert any(r["needs"]["vram_gb"] == 0 for r in routes), "没有零显存路线"

    good_src = os.path.join(ROOT, "routes", "puppet", "route.json")
    if not os.path.isfile(good_src):
        print("⚠️  跳过坏契约部分：routes/puppet/route.json 不在这里")
        print(f"✅ test_route 部分通过：{len(routes)} 条契约")
        return 0
    good = json.load(open(good_src, encoding="utf-8"))
    blocked = 0
    for label, mutate in CASES:
        r = json.loads(json.dumps(good))          # 深拷贝
        try:
            mutate(r)
        except (KeyError, IndexError):
            continue                              # 这条路线没有该槽位，跳过
        try:
            route.validate(r, "test/route.json")
            raise AssertionError(f"{label}：应该报错却通过了")
        except route.RouteError:
            blocked += 1
    assert blocked >= 6, f"只拦住了 {blocked} 种坏契约，校验器太松"
    print(f"✅ test_route 全过：{len(routes)} 条真契约 + {blocked} 种坏契约被拦住")
    return 0


if __name__ == "__main__":
    sys.exit(main())
