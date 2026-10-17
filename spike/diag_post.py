# -*- coding: utf-8 -*-
"""诊断：同一 payload，urllib POST 为何 404 —— 打印状态码与响应体"""
import json
import urllib.error
import urllib.request

URL = "http://127.0.0.1:7860/sdapi/v1/txt2img"
BODY = {"prompt": "1girl, anime, solo", "width": 512, "height": 512,
        "steps": 4, "cfg_scale": 2, "sampler_name": "Euler a", "seed": 1}

print("--- A) 当前 urllib 默认头 ---")
req = urllib.request.Request(URL, data=json.dumps(BODY).encode("utf-8"),
                             headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=300) as r:
        print("   状态:", r.status, "长度:", len(r.read()))
except urllib.error.HTTPError as e:
    print("   HTTPError:", e.code, e.reason)
    print("   响应体:", e.read()[:300])
except Exception as e:
    print("   其他异常:", type(e).__name__, e)

print("--- B) 伪装成 curl 的头（去掉 Python-urllib UA）---")
req2 = urllib.request.Request(
    URL, data=json.dumps(BODY).encode("utf-8"),
    headers={"Content-Type": "application/json",
             "User-Agent": "curl/8.4.0", "Accept": "*/*"})
try:
    with urllib.request.urlopen(req2, timeout=300) as r:
        print("   状态:", r.status, "长度:", len(r.read()))
except urllib.error.HTTPError as e:
    print("   HTTPError:", e.code, e.reason)
    print("   响应体:", e.read()[:300])
except Exception as e:
    print("   其他异常:", type(e).__name__, e)
