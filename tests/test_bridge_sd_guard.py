# -*- coding: utf-8 -*-
"""aigc-bridge 的 sd_start 单实例守卫（真跑 MCP stdio，不 mock）。

做法：起一个假的监听进程占住 SD 端口 → 通过 JSON-RPC 调 sd_start → 看它是否拒绝。

要求 server.mjs 所在目录已 `npm install`（有 node_modules），默认取仓库里的
app/bridge；在跑的 Hermes 桥在 profiles/<profile>/mcp/aigc-bridge/，用参数指过去：

    python tests/test_bridge_sd_guard.py "E:/.../aigc-bridge/server.mjs"
"""
import json
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "bridge", "server.mjs")
PORT = int(os.environ.get("SD_GUARD_TEST_PORT", "7860"))


def listener_alive():
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def start_dummy():
    code = (
        "import socket,time\n"
        "s=socket.socket()\n"
        "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)\n"
        f"s.bind(('127.0.0.1',{PORT}))\n"
        "s.listen(5)\n"
        "time.sleep(120)\n"
    )
    p = subprocess.Popen([sys.executable, "-c", code])
    for _ in range(100):
        if listener_alive():
            return p
        time.sleep(0.1)
    p.kill()
    raise SystemExit("假监听没起来")


def mcp_call():
    p = subprocess.Popen(["node", BRIDGE], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, encoding="utf-8")
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "guard-test", "version": "1.0"}}}
    notes = [{"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}]
    call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "sd_start", "arguments": {}}}
    payload = "\n".join(json.dumps(x, ensure_ascii=False) for x in [init] + notes + [call]) + "\n"
    try:
        out, err = p.communicate(payload, timeout=90)
    except subprocess.TimeoutExpired:
        p.kill()
        out, err = p.communicate()
    reply = None
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("id") == 2:
            reply = msg
    return reply, err


def main():
    print(f"bridge={BRIDGE}")
    assert os.path.isfile(BRIDGE), "找不到 server.mjs"
    assert not listener_alive(), f"端口 {PORT} 本来就有人听着，先清干净再测"
    dummy = start_dummy()
    try:
        print(f"假监听已占住 :{PORT}（pid={dummy.pid}）")
        reply, err = mcp_call()
        assert reply, f"sd_start 没有回话\nstderr={err[:800]}"
        text = "".join(c.get("text", "") for c in reply.get("result", {}).get("content", []))
        print("sd_start 回复：\n  " + text.replace("\n", "\n  "))
        assert "already listening" in text.lower(), f"没拦住第二份：{text!r}"
        assert str(dummy.pid) in text, f"没报出占用者 pid：{text!r}"
        assert dummy.poll() is None, "守卫把别人的进程杀了？"
        print("\nBRIDGE GUARD OK · 端口被占时 sd_start 拒绝起第二份")
    finally:
        dummy.kill()


if __name__ == "__main__":
    main()
