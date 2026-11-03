# -*- coding: utf-8 -*-
"""假内核：只会打握手、报三次进度、响应 /healthz。

它的存在意义只有一个——证明壳的管道能起、能判活、能收尸，全程不碰任何真后端。
"""
import http.server
import sys
import threading
import time

port = int(sys.argv[1]) if len(sys.argv) > 1 else 18899


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        code = 200 if self.path == "/healthz" else 404
        body = b'{"ok": true, "fake": true}'
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = http.server.HTTPServer(("127.0.0.1", port), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
print(f"PIPE:READY port={port}", flush=True)
for i in range(1, 4):
    time.sleep(0.3)
    print(f"PIPE:PROGRESS {i / 3:.2f} 假进度 {i}/3", flush=True)
print("PIPE:LOG 假内核就绪，等壳收尸", flush=True)

try:
    while True:
        time.sleep(0.5)
except KeyboardInterrupt:
    pass
