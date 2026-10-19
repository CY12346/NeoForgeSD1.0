# -*- coding: utf-8 -*-
"""github.com 直连被重置时，用 GitHub API（api.github.com 可通）推一次提交 + 打 tag。

原理：Git Data API（blobs → tree → commit → ref），一次 commit 推完本地全部文件。
安全检查：远端 head 必须是本地 HEAD 的祖先，否则拒绝（防止覆盖别人的提交）。
用法：python tools/_push_via_api.py [--dry]
"""
import base64
import json
import subprocess
import sys
import urllib.error
import urllib.request

OWNER, REPO = "CY12346", "NeoForgeSD1.0"
API = "https://api.github.com"
DRY = "--dry" in sys.argv


def token():
    r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
    return r.stdout.strip()


TOK = token()
if not TOK:
    sys.exit("没有 gh token（先 gh auth login）")


def api(method, path, body=None):
    req = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + TOK,
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "neoforge-push", "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.exit("API %s %s → %s %s" % (method, path, e.code, e.read()[:400]))


head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
ref = api("GET", "/repos/%s/%s/git/ref/heads/main" % (OWNER, REPO))
remote = ref["object"]["sha"]
print("本地 HEAD:", head[:12], " 远端 main:", remote[:12])

anc = subprocess.run(["git", "merge-base", "--is-ancestor", remote, head]).returncode == 0
if not anc and remote != head:
    # 远端有本地没有的提交（网页上直接改过 README 这类）：用远端当 parent，
    # 本地 tree 覆盖内容 —— 必须先确认那些改动已经合并进本地文件，否则会丢。
    print("⚠️ 远端领先本地（%s 不是本地祖先），将以远端 head 作为父提交" % remote[:12])
    if "--force-parent" not in sys.argv:
        sys.exit("加 --force-parent 才继续（确认远端改动已合并到本地）")

if remote == head:
    print("远端已是最新，跳过 commit")

out = subprocess.run(["git", "ls-tree", "-r", "--full-tree", head],
                     capture_output=True, text=True).stdout
entries = []
for ln in out.splitlines():
    meta, path = ln.split("\t", 1)
    mode, typ, sha = meta.split()
    if typ == "blob":
        entries.append((mode, path, sha))
print("文件数:", len(entries))
total = sum(len(subprocess.run(["git", "cat-file", "blob", s], capture_output=True).stdout)
            for _, _, s in entries)
print("总字节:", total)

if DRY:
    sys.exit(0)

tree = []
for i, (mode, path, sha) in enumerate(entries, 1):
    blob = subprocess.run(["git", "cat-file", "blob", sha], capture_output=True).stdout
    r = api("POST", "/repos/%s/%s/git/blobs" % (OWNER, REPO),
            {"content": base64.b64encode(blob).decode(), "encoding": "base64"})
    tree.append({"path": path, "mode": mode, "type": "blob", "sha": r["sha"]})
    if i % 10 == 0:
        print("  blob %d/%d" % (i, len(entries)))

new_tree = api("POST", "/repos/%s/%s/git/trees" % (OWNER, REPO), {"tree": tree})
print("tree:", new_tree["sha"][:12])

msg = subprocess.run(["git", "log", "-1", "--pretty=%B"], capture_output=True,
                     text=True, encoding="utf-8").stdout.strip()
commit = api("POST", "/repos/%s/%s/git/commits" % (OWNER, REPO),
             {"message": msg, "tree": new_tree["sha"], "parents": [remote]})
print("commit:", commit["sha"][:12])

api("PATCH", "/repos/%s/%s/git/refs/heads/main" % (OWNER, REPO),
    {"sha": commit["sha"], "force": False})
print("main 已更新")

try:
    api("POST", "/repos/%s/%s/git/refs" % (OWNER, REPO),
        {"ref": "refs/tags/v1.1.0", "sha": commit["sha"]})
    print("tag v1.1.0 已创建")
except SystemExit as e:
    print("tag:", e)

# 回读验证
chk = api("GET", "/repos/%s/%s/git/ref/heads/main" % (OWNER, REPO))
print("远端 main 现在:", chk["object"]["sha"][:12])
tr = api("GET", "/repos/%s/%s/git/trees/%s?recursive=1" % (OWNER, REPO, new_tree["sha"]))
print("远端文件数:", len([x for x in tr.get("tree") or [] if x.get("type") == "blob"]))
