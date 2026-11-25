# -*- coding: utf-8 -*-
"""Git 凭据助手: 从 Windows 凭据管理器读取 github.com 的账号/令牌。

用法(仓库内一次性配置):
    git config credential.helper '!"<python.exe>" "<此脚本路径>"'
之后 git fetch / git push 会自动取用, 不会弹框, 令牌也不写入 .git/config。

Git 调用约定: 从 stdin 读 "get"/"store"/"erase"; get 时在 stdout 打印:
    username=...
    password=...
"""
import ctypes
import sys
from ctypes import wintypes

CRED_TYPE_GENERIC = 1
TARGETS = ("git:https://github.com",
           "LegacyGeneric:target=git:https://github.com")


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


def read_credential():
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.POINTER(ctypes.POINTER(CREDENTIALW))]
    advapi.CredReadW.restype = wintypes.BOOL
    advapi.CredFree.argtypes = [ctypes.c_void_p]
    for target in TARGETS:
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
            user = c.UserName or ""
            if secret.strip():
                return user, secret.strip()
        finally:
            advapi.CredFree(p)
    return None


def main():
    # shell 型 helper(`!cmd`) 会把操作当成第一个参数传进来; 普通 helper 走 stdin
    op = sys.argv[1].strip() if len(sys.argv) > 1 else (sys.stdin.readline() or "").strip()
    if op != "get":
        return 0
    cred = read_credential()
    if not cred:
        return 1
    user, password = cred
    sys.stdout.write("username=%s\npassword=%s\n\n" % (user.replace("\n", ""), password.replace("\n", "")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
