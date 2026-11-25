# -*- coding: utf-8 -*-
"""通过 GitHub REST API 提交当前目录 (适合 github.com:443 连不上、但 api.github.com 能通的环境)。

用法:
    python tools\\push_via_api.py                 # 提交到 <用户名>/<目录名>
    python tools\\push_via_api.py --repo QuickLauncher --message "fix: ..."
    python tools\\push_via_api.py --dry-run       # 只列出会提交哪些文件

凭据来源(按顺序):
    1. 环境变量 GITHUB_TOKEN (或 GH_TOKEN)
    2. Windows 凭据管理器里的 git:https://github.com
    3. git credential fill (若可用)

注意: 只提交当前目录下的代码, 会按下面的 SKIP 规则排除本机配置/日志/构建产物。
"""
import argparse
import base64
import ctypes
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path

API = "https://api.github.com"
UA = "quicklauncher-push"

SKIP_DIRS = {".git", "data", "build", "dist", "release", "__pycache__", ".vs", ".idea", ".vscode"}
SKIP_NAMES = {"config.json", "Thumbs.db", "desktop.ini", ".gitignore.bak"}
SKIP_EXTS = {".exe", ".zip", ".pyc", ".pyo", ".spec", ".log", ".ico.tmp"}


# ---------------------------------------------------------------- 凭据
CRED_TYPE_GENERIC = 1


class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _cred_from_windows():
    if sys.platform != "win32":
        return None
    try:
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        advapi.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                     ctypes.POINTER(ctypes.POINTER(CREDENTIALW))]
        advapi.CredReadW.restype = wintypes.BOOL
        advapi.CredFree.argtypes = [ctypes.c_void_p]
        for target in ("git:https://github.com", "LegacyGeneric:target=git:https://github.com"):
            p = ctypes.POINTER(CREDENTIALW)()
            if not advapi.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(p)):
                continue
            try:
                c = p.contents
                raw = ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize)
                try:
                    secret = raw.decode("utf-16-le")
                except Exception:
                    secret = raw.decode("utf-8", "replace")
                if secret.strip():
                    return secret.strip()
            finally:
                advapi.CredFree(p)
    except Exception:
        pass
    return None


def _cred_from_git():
    try:
        p = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True, text=True, timeout=15,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
        secret = ""
        for line in p.stdout.splitlines():
            if line.startswith("password="):
                secret = line.split("=", 1)[1].strip()
        return secret or None
    except Exception:
        return None


def get_token():
    for env in ("GITHUB_TOKEN", "GH_TOKEN"):
        v = os.environ.get(env)
        if v:
            return v.strip(), env
    v = _cred_from_windows()
    if v:
        return v, "Windows 凭据管理器"
    v = _cred_from_git()
    if v:
        return v, "git credential"
    return None, None


# ---------------------------------------------------------------- API
def api(path, token, method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        API + path, data=data, method=method,
        headers={
            "Authorization": "token " + token,
            "Accept": "application/vnd.github+json",
            "User-Agent": UA,
            "Content-Type": "application/json",
        })
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            body = r.read().decode() or "{}"
            return r.status, json.loads(body)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(body or "{}")
        except Exception:
            return e.code, {"message": body[:300]}


# ---------------------------------------------------------------- 收集文件
def collect(root):
    files = []
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".git")]
        for name in filenames:
            if name in SKIP_NAMES:
                continue
            if os.path.splitext(name)[1].lower() in SKIP_EXTS:
                continue
            p = Path(dirpath) / name
            rel = p.relative_to(root).as_posix()
            files.append((rel, p))
    return sorted(files, key=lambda x: x[0])


def main():
    ap = argparse.ArgumentParser(description="用 GitHub API 推送当前目录")
    ap.add_argument("--repo", default="", help="仓库名 (默认取目录名)")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--message", default="", help="提交说明")
    ap.add_argument("--private", action="store_true", help="创建私有仓库")
    ap.add_argument("--description", default="Windows 托盘快捷启动台：应用启动 / 模式切换 / 网络 / 音量 / 分辨率")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    files = collect(root)
    if not files:
        print("没有可提交的文件")
        return 1

    print("项目目录: %s" % root)
    print("待提交 %d 个文件:" % len(files))
    for rel, p in files:
        print("  %-40s %7.1f KB" % (rel, p.stat().st_size / 1024))
    if args.dry_run:
        return 0

    token, src = get_token()
    if not token:
        print("\n没找到 GitHub 凭据。设置 GITHUB_TOKEN 环境变量, 或登录一次 git。")
        return 2
    print("\n凭据来源: %s" % src)

    st, me = api("/user", token)
    if st != 200:
        print("凭据无效: %s %s" % (st, me.get("message")))
        return 3
    owner = me["login"]
    repo = args.repo or root.name
    print("账号: %s | 仓库: %s/%s | 分支: %s" % (owner, owner, repo, args.branch))

    st, info = api("/repos/%s/%s" % (owner, repo), token)
    if st == 404:
        st2, created = api("/user/repos", token, "POST", {
            "name": repo,
            "description": args.description,
            "private": bool(args.private),
            "auto_init": False,
        })
        if st2 not in (200, 201):
            print("创建仓库失败: %s %s" % (st2, created.get("message")))
            return 4
        print("已创建仓库:", created.get("html_url"))
        info = created
    elif st != 200:
        print("读取仓库失败: %s %s" % (st, info.get("message")))
        return 4

    # 当前分支的最新提交 (用于父提交)
    parent = None
    st, ref = api("/repos/%s/%s/git/ref/heads/%s" % (owner, repo, args.branch), token)
    if st == 200:
        parent = ref["object"]["sha"]
        print("父提交:", parent[:10])
    else:
        # 空仓库: git 数据接口用不了, 先用 Contents 接口放一个占位文件把仓库初始化出来
        st, boot = api("/repos/%s/%s/contents/.bootstrap" % (owner, repo), token, "PUT", {
            "message": "chore: initialize repository",
            "content": base64.b64encode(b" ").decode(),
        })
        if st not in (200, 201):
            print("初始化空仓库失败: %s %s" % (st, boot.get("message")))
            return 5
        parent = boot["commit"]["sha"]
        st, ref = api("/repos/%s/%s/git/ref/heads/%s" % (owner, repo, args.branch), token)
        if st == 200:
            parent = ref["object"]["sha"]
        print("空仓库已初始化, 父提交:", parent[:10])

    # 上传每个文件成 blob
    tree = []
    for rel, p in files:
        raw = p.read_bytes()
        content = base64.b64encode(raw).decode()
        st, blob = api("/repos/%s/%s/git/blobs" % (owner, repo), token, "POST",
                       {"content": content, "encoding": "base64"})
        if st not in (200, 201):
            print("上传失败 %s: %s %s" % (rel, st, blob.get("message")))
            return 5
        tree.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        print("  + %s" % rel)

    st, tree_obj = api("/repos/%s/%s/git/trees" % (owner, repo), token, "POST", {"tree": tree})
    if st not in (200, 201):
        print("建树失败: %s %s" % (st, tree_obj.get("message")))
        return 6

    msg = args.message or "Update %d files" % len(files)
    payload = {"message": msg, "tree": tree_obj["sha"]}
    if parent:
        payload["parents"] = [parent]
    st, commit = api("/repos/%s/%s/git/commits" % (owner, repo), token, "POST", payload)
    if st not in (200, 201):
        print("提交失败: %s %s" % (st, commit.get("message")))
        return 7

    if parent:
        st, upd = api("/repos/%s/%s/git/refs/heads/%s" % (owner, repo, args.branch),
                      token, "PATCH", {"sha": commit["sha"], "force": False})
    else:
        st, upd = api("/repos/%s/%s/git/refs" % (owner, repo), token, "POST",
                      {"ref": "refs/heads/%s" % args.branch, "sha": commit["sha"]})
    if st not in (200, 201):
        print("更新分支失败: %s %s" % (st, upd.get("message")))
        return 8

    print("\n已提交: %s" % commit["sha"])
    print("仓库地址: %s" % info.get("html_url"))
    print("提交说明: %s" % msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
