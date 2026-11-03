# -*- coding: utf-8 -*-
"""任务层：一次出图/重绘/放大 = 一个 job。互不阻塞，靠 /api/job/<id> 轮询。

并发约束：4 GB 卡一次只能跑一张，所以这里不设并行上限——SD 后端自己排队；
这里只保证「提交即返回 job_id」，页面不会卡在请求里。
"""
import base64
import json
import os
import threading
import time

from . import config, history, sdapi

JOBS = {}
JOBS_LOCK = threading.Lock()
KEEP = 60           # 内存里保留最近多少个 job（页面只看当前这一个）


def _remember(job):
    with JOBS_LOCK:
        JOBS[job["id"]] = job
        if len(JOBS) > KEEP:
            for old in sorted(JOBS.values(), key=lambda j: j.get("created") or 0)[:-KEEP]:
                JOBS.pop(old["id"], None)


def get(jid):
    with JOBS_LOCK:
        job = JOBS.get(jid)
        return dict(job) if job else None


def _seed_of(resp, payload):
    try:
        return json.loads(resp.get("info") or "{}").get("seed", payload.get("seed"))
    except Exception:
        return payload.get("seed")


def _run(job, body, kind):
    t0 = time.time()
    job["state"] = "running"
    try:
        if kind == "upscale":
            r = sdapi.post("/sdapi/v1/extra-single-image", body)
            raw = base64.b64decode((r.get("image") or "").split(",")[-1])
            p = os.path.join(config.WORK, f"{job['id']}_up.png")
            with open(p, "wb") as f:
                f.write(raw)
            job["files"] = [p]
        else:
            path = "/sdapi/v1/txt2img" if kind == "image" else "/sdapi/v1/img2img"
            resp = sdapi.post(path, body)
            want = int(body.get("n_iter") or 1)
            files = []
            for i, b in enumerate((resp.get("images") or [])[:want]):
                p = os.path.join(config.WORK, f"{job['id']}_{i:02d}.png")
                with open(p, "wb") as f:
                    f.write(base64.b64decode(b.split(",")[-1]))
                files.append(p)
            job["files"] = files
            job["seed"] = _seed_of(resp, body)
        job["image_urls"] = ["/files/" + os.path.basename(p) for p in job.get("files") or []]
        job["elapsed"] = round(time.time() - t0, 1)
        job["state"] = "done"
        try:
            history.add({
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind,
                "files": [os.path.basename(p) for p in job.get("files") or []],
                "prompt": body.get("prompt") or "", "negative": body.get("negative_prompt") or "",
                "width": body.get("width"), "height": body.get("height"),
                "steps": body.get("steps"), "cfg": body.get("cfg_scale"),
                "sampler": body.get("sampler_name"), "scheduler": body.get("scheduler"),
                "seed": job.get("seed"), "denoise": body.get("denoising_strength"),
                "elapsed": job["elapsed"],
                "params": {k: v for k, v in body.items()
                           if k in ("prompt", "negative_prompt", "width", "height", "steps",
                                    "cfg_scale", "sampler_name", "scheduler", "denoising_strength")},
            })
        except Exception as e:
            job["history_error"] = str(e)[:160]
    except Exception as e:
        job["state"] = "error"
        job["error"] = f"{type(e).__name__}: {str(e)[:400]}"
        job["elapsed"] = round(time.time() - t0, 1)


def submit(kind, body):
    """提交一个任务，立刻返回 job_id。body 是 payload.build_* 已经装好的载荷。"""
    jid = os.urandom(6).hex()
    job = {"id": jid, "kind": kind, "state": "queued", "created": time.time(),
           "params": {k: body.get(k) for k in
                      ("prompt", "width", "height", "steps", "cfg_scale",
                       "sampler_name", "scheduler", "n_iter", "denoising_strength")}}
    _remember(job)
    threading.Thread(target=_run, args=(job, body, kind), daemon=True).start()
    return jid
