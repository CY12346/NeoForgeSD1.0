# -*- coding: utf-8 -*-
"""路线登记册（route）：只认 route.json，不认路线代码。

和 kernel.py 的分工——别混：
    kernel  = 一个**可启动的推理后端进程**：有端口、有健康检查方言、有收尸。
    route   = 一条**生成路线**：输入槽 → 若干步骤 → 输出槽。它是纯流水线，**没有端口**。
    所以路线不能塞进 kernel.json（那里 port 是必填），两者是并列的两层。

设计约束（沿用 SPEC 那套）：
  - spec N 与 N-1 兼容，别的版本直接报人话错误
  - 校验失败不兜底、不静默，一次把问题说清
  - 契约不撒谎：代码不在现役仓库时必须写 impl.status="parked" 并给出真实位置

单独跑：
    python -m core.route              # 列出所有路线 + 校验结果
    python -m core.route --json       # 机器读
    python -m core.route puppet       # 只看一条，打印完整契约
"""
import json
import os
import sys

SPEC = 1
SUPPORTED_SPECS = (0, 1)

# 槽位类型白名单：新增类型要同时想清楚「谁是消费方」，别乱加
SLOT_TYPES = ("image", "video", "audio", "text", "number", "int", "bool", "enum", "path")
REQUIRED = ("spec", "id", "name", "inputs", "outputs", "steps")
# impl.status —— 契约必须如实反映代码在哪
IMPL_STATES = ("active", "parked", "stub")


class RouteError(ValueError):
    """route.json 不合规——错误信息要能直接给人看。"""


# ------------------------------------------------------------------ 校验
def _check_slot(slot, where):
    if not isinstance(slot, dict):
        raise RouteError(f"{where}：每个槽位必须是对象，实际是 {type(slot).__name__}")
    for k in ("name", "type"):
        if k not in slot:
            raise RouteError(f"{where}：槽位缺少必填字段 {k}（{slot}）")
    if slot["type"] not in SLOT_TYPES:
        raise RouteError(
            f"{where}：槽位 {slot['name']} 的 type={slot['type']!r} 不在支持范围 {SLOT_TYPES}")
    if slot["type"] == "enum" and not slot.get("options"):
        raise RouteError(f"{where}：枚举槽位 {slot['name']} 必须给 options")


def validate(r, path=""):
    """校验一份路线契约，返回规范化后的 dict（补默认值）；不合规抛 RouteError。"""
    where = path or r.get("id") or "route.json"
    if not isinstance(r, dict):
        raise RouteError(f"{where}：顶层必须是 JSON 对象")
    for k in REQUIRED:
        if k not in r:
            raise RouteError(f"{where}：缺少必填字段 {k}")

    spec = r.get("spec")
    if spec not in SUPPORTED_SPECS:
        raise RouteError(
            f"{where}：spec={spec} 不在支持范围 {SUPPORTED_SPECS}——"
            f"这份 route.json 可能是更新版本写的，请升级启动器")

    for k in ("id", "name"):
        if not isinstance(r[k], str) or not r[k].strip():
            raise RouteError(f"{where}：{k} 必须是非空字符串")

    ins = r["inputs"]
    outs = r["outputs"]
    if not isinstance(ins, list):
        raise RouteError(f"{where}：inputs 必须是数组")
    if not isinstance(outs, list) or not outs:
        raise RouteError(f"{where}：outputs 必须是非空数组（路线至少产出一个东西）")
    for i, s in enumerate(ins):
        _check_slot(s, f"{where}.inputs[{i}]")
    for i, s in enumerate(outs):
        _check_slot(s, f"{where}.outputs[{i}]")
    # 同名槽位会让人分不清，直接报
    names = [s["name"] for s in ins]
    dup = {n for n in names if names.count(n) > 1}
    if dup:
        raise RouteError(f"{where}：输入槽位重名 {sorted(dup)}")

    steps = r["steps"]
    if not isinstance(steps, list) or not steps:
        raise RouteError(f"{where}：steps 必须是非空数组")
    for i, st in enumerate(steps):
        if not isinstance(st, dict):
            raise RouteError(f"{where}.steps[{i}]：必须是对象")
        if not st.get("tool"):
            raise RouteError(f"{where}.steps[{i}]：缺少 tool（这一步由谁执行）")
        if "args" in st and not isinstance(st["args"], dict):
            raise RouteError(f"{where}.steps[{i}]：args 必须是对象")

    needs = r.get("needs") or {}
    if not isinstance(needs, dict):
        raise RouteError(f"{where}：needs 必须是对象")
    vram = needs.get("vram_gb", 0)
    if not isinstance(vram, (int, float)) or vram < 0:
        raise RouteError(f"{where}：needs.vram_gb 必须是非负数字（没有显存需求就写 0）")

    impl = r.get("impl") or {}
    if impl:
        st = impl.get("status")
        if st not in IMPL_STATES:
            raise RouteError(f"{where}：impl.status={st!r} 不在 {IMPL_STATES} 之内")
        if st == "parked" and not impl.get("path"):
            raise RouteError(f"{where}：impl.status=parked 时必须给出 impl.path（归档在哪）")

    out = dict(r)
    out["needs"] = {"vram_gb": vram,
                    "gpu": bool(needs.get("gpu", vram > 0)),
                    "requires": list(needs.get("requires") or [])}
    out["impl"] = {"status": impl.get("status", "stub"),
                   "path": impl.get("path", ""),
                   "entry": impl.get("entry", "")}
    return out


def load(path):
    """读并校验一份 route.json。"""
    path = os.path.abspath(path)
    try:
        with open(path, encoding="utf-8") as f:
            r = json.load(f)
    except FileNotFoundError:
        raise RouteError(f"找不到 {path}")
    except json.JSONDecodeError as e:
        raise RouteError(f"route.json 不是合法 JSON：{e}（{path}）")
    return validate(r, os.path.basename(os.path.dirname(path)) + "/route.json")


# ------------------------------------------------------------------ 发现
def discover(root, base_dir=None):
    """扫 <root>/routes/*/route.json → (可用列表, 坏件说明列表)。"""
    routes, bad = [], []
    rroot = os.path.join(root, "routes")
    if not os.path.isdir(rroot):
        return routes, bad
    for rid in sorted(os.listdir(rroot)):
        rdir = os.path.join(rroot, rid)
        mf = os.path.join(rdir, "route.json")
        if not os.path.isfile(mf):
            continue
        try:
            r = load(mf)
        except RouteError as e:
            bad.append(str(e))
            continue
        if base_dir:
            r = _resolve(r, base_dir, rdir)
        routes.append(r)
    return routes, bad


def _resolve(r, base_dir, route_dir):
    """把 {base} / {route} / {impl} 占位符换掉；不认得的占位符原样留着，让人看见。

    顺序有讲究：先解析 impl，再把 impl.path 当成 {impl} 供 steps 用 —— 这样
    代码在归档里时 step.entry 指向真实位置，取回后只改 impl.path 一处，步骤自动跟着。
    """
    ph = {"{base}": base_dir, "{route}": os.path.normpath(route_dir)}

    def walk(v, table):
        if isinstance(v, str):
            out = v
            for k, val in table.items():
                out = out.replace(k, val)
            if ("/" in out or "\\" in out) and "{" not in out:
                out = os.path.normpath(out)
            return out
        if isinstance(v, list):
            return [walk(x, table) for x in v]
        if isinstance(v, dict):
            return {k: walk(x, table) for k, x in v.items()}
        return v

    r = dict(r)
    r["impl"] = walk(r.get("impl") or {}, ph)
    ph["{impl}"] = r["impl"].get("path", "")
    r["outputs"] = walk(r.get("outputs") or [], ph)
    r["steps"] = walk(r.get("steps") or [], ph)
    return r


# ------------------------------------------------------------------ 展示
def route_md(r):
    """一行摘要，给自检/列表用。"""
    vram = r["needs"]["vram_gb"]
    impl = r["impl"]["status"]
    flag = {"active": "现役", "parked": "已归档", "stub": "未实现"}.get(impl, impl)
    return (f"{r['id']} · {r['name']}｜输入 {len(r['inputs'])} 槽 / "
            f"步骤 {len(r['steps'])} / 显存 {vram} GB｜{flag}")


def dump(r):
    """完整契约的人话打印。"""
    lines = [f"# {r['id']} · {r['name']}", ""]
    if r.get("notes"):
        lines += [r["notes"], ""]
    nb = r["needs"]
    lines += [f"- spec：{r['spec']}", f"- 显存：{nb['vram_gb']} GB"
              + ("（要 GPU）" if nb["gpu"] else "（纯 CPU，可与 SD 同开）"),
              f"- 实现：{r['impl']['status']}"
              + (f" · {r['impl']['path']}" if r["impl"]["path"] else ""),
              "", "## 输入"]
    for s in r["inputs"]:
        req = "必填" if s.get("required") else f"默认 {s.get('default')!r}"
        opts = ("  可选：" + "/".join(s["options"])) if s.get("options") else ""
        lines.append(f"- `{s['name']}` ({s['type']}, {req}) {s.get('desc', '')}{opts}")
    lines += ["", "## 输出"]
    for s in r["outputs"]:
        lines.append(f"- `{s['name']}` ({s['type']}) {s.get('desc', '')}")
    lines += ["", "## 步骤"]
    for i, st in enumerate(r["steps"], 1):
        lines.append(f"{i}. {st['tool']}"
                     + (f" → {st['entry']}" if st.get("entry") else "")
                     + (f"  args={json.dumps(st['args'], ensure_ascii=False)}" if st.get("args") else ""))
    return "\n".join(lines)


def main(argv):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core import selfcheck   # 借它的 base_of，别重复实现
    base = selfcheck.base_of(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    as_json = "--json" in argv
    only = next((a for a in argv if not a.startswith("-")), None)

    routes, bad = [], []
    for root in (base, os.path.join(base, "app")):
        rs, bs = discover(root, base_dir=base)
        for r in rs:
            if r["id"] not in {x["id"] for x in routes}:
                routes.append(r)
        bad += bs

    if as_json:
        print(json.dumps({"base": base, "routes": routes, "bad": bad},
                         ensure_ascii=False, indent=1))
        return 0 if not bad else 2

    print(f"NeoForgeSD 路线登记册 · 根目录 {base}")
    if not routes and not bad:
        print("\n（还没有任何路线。建 routes/<id>/route.json，格式见 core/route.py 顶部说明）")
        return 0
    for r in routes:
        if only and r["id"] != only:
            continue
        print("\n" + (dump(r) if only else "· " + route_md(r)))
    if bad:
        print("\n坏掉的契约：")
        for b in bad:
            print(f"  ❌ {b}")
    return 0 if not bad else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
