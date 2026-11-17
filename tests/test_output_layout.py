# -*- coding: utf-8 -*-
"""产物落点测试：work/outputs/<种类>/<年-月-日>/，文生图与图生图不混，打开目录按种类走。

跑法（Windows 本机）：
    set NEOFORGE_SILENT=1 && python tests/test_output_layout.py
"""
import base64
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "kernels", "neoforge-sd15"))

TMP = tempfile.mkdtemp(prefix="nfsd-out-")
os.environ["NEOFORGE_IMAGE_WORK"] = TMP
os.environ["NEOFORGE_SILENT"] = "1"

from neoforge import config, jobs, server, sdapi   # noqa: E402

# 1×1 PNG，够当"SD 回来的图"
PNG = base64.b64encode(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)).decode()


def _fake_post(path, body, timeout=None):
    """替掉 sdapi.post：只按端点回一张图，不碰网络。"""
    if path.endswith("/txt2img"):
        return {"images": [PNG], "info": json.dumps({"seed": 11})}
    if path.endswith("/img2img"):
        return {"images": [PNG], "info": json.dumps({"seed": 22})}
    if path.endswith("/extra-single-image"):
        return {"image": PNG}
    raise AssertionError("没料到的端点：" + path)


class OutputLayout(unittest.TestCase):
    def setUp(self):
        self._orig_post = sdapi.post
        sdapi.post = _fake_post
        self.today = __import__("time").strftime("%Y-%m-%d")

    def tearDown(self):
        sdapi.post = self._orig_post

    def _run_once(self, kind, body, jid):
        job = {"id": jid, "kind": kind, "state": "queued", "created": 0}
        jobs._run(job, body, kind)
        return job

    def test_dir_shape(self):
        p = config.out_dir("txt2img")
        self.assertTrue(p.endswith(os.path.join("outputs", "txt2img", self.today)), p)
        self.assertTrue(os.path.isdir(p))
        self.assertEqual(config.out_dir("image"), p)          # 别名指向同一格
        self.assertTrue(config.out_dir("").endswith("outputs"))

    def test_txt2img_lands_in_its_own_folder(self):
        job = self._run_once("image", {"prompt": "cat", "n_iter": 1}, "aaa111")
        self.assertEqual(job["state"], "done", job.get("error"))
        self.assertEqual(job["sub"], "txt2img")
        self.assertTrue(job["folder"].endswith(os.path.join("outputs", "txt2img", self.today)))
        f = job["files"][0]
        self.assertTrue(os.path.isfile(f), f)
        self.assertEqual(job["image_urls"][0], "/files/" + job["rel"][0])
        self.assertEqual(job["rel"][0], "outputs/txt2img/%s/aaa111_00.png" % self.today)
        self.assertNotIn("\\", job["rel"][0])                   # 统一正斜杠
        hist = json.load(open(config.HISTORY_FILE, encoding="utf-8"))
        self.assertEqual(hist[0]["files"], job["rel"])
        self.assertEqual(hist[0]["sub"], "txt2img")

    def test_img2img_and_inpaint_do_not_share(self):
        i2i = self._run_once("edit", {"prompt": "dog", "image_b64": "x", "n_iter": 1}, "bbb222")
        inp = self._run_once("edit", {"prompt": "fix", "image_b64": "x", "mask": "m",
                                      "n_iter": 1}, "ccc333")
        up = self._run_once("upscale", {"image": "x"}, "ddd444")
        self.assertEqual((i2i["sub"], inp["sub"], up["sub"]),
                         ("img2img", "inpaint", "upscale"))
        self.assertNotEqual(os.path.dirname(i2i["files"][0]), os.path.dirname(inp["files"][0]))
        for job in (i2i, inp, up):
            self.assertTrue(os.path.isfile(job["files"][0]))

    def test_files_route_and_traversal(self):
        job = self._run_once("image", {"prompt": "cat", "n_iter": 1}, "eee555")
        srv = server.make_server(port=0)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        base = "http://127.0.0.1:%d" % srv.server_address[1]
        try:
            with urllib.request.urlopen(base + job["image_urls"][0], timeout=5) as r:
                self.assertEqual(r.status, 200)
                self.assertEqual(r.headers.get("Content-Type"), "image/png")
            for bad in ("/files/../history_image.json", "/files/..%2f..%2fetc%2fpasswd",
                        "/files/outputs/txt2img/../../history_image.json"):
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    urllib.request.urlopen(base + bad, timeout=5)
                self.assertEqual(cm.exception.code, 404, bad)
            req = urllib.request.Request(
                base + "/api/open_folder", data=json.dumps({"kind": "img2img"}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                got = json.loads(r.read().decode("utf-8"))
            self.assertTrue(got["ok"], got)
            self.assertTrue(got["folder"].endswith(os.path.join("outputs", "img2img", self.today)),
                            got["folder"])
            # 自动建目录：今天还没出过图的种类，打开也该有个空目录
            self.assertTrue(os.path.isdir(got["folder"]))
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
