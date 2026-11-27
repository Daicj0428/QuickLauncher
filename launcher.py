# -*- coding: utf-8 -*-
"""
快捷启动台 QuickLauncher v1.4
托盘常驻工具：
  · 🚀 快速切换模式（右键托盘）: 一键切换自定义模式——自动应用该模式保存的
    屏幕分辨率/刷新率、音量设置，启动该组应用，并停掉上一模式启动的进程
  · ⚙ 整体编辑窗口（双击托盘图标）: 图形化编辑应用(带图标显示) / 模式(含显示与
    音量设置) / 网络 / 音量 / 显示 / 系统；最小化或点 ✕ 隐藏到托盘
  · 自定义快捷启动多个应用（启动时自动获取本机所有非系统内置应用）
  · 快速调节网络（代理 / DNS / WiFi / 网卡启停）
  · 启动时自动检测本机应用与系统配置情况，生成检测报告并弹窗摘要
  · 支持开机自启、以管理员身份重启、托盘图标自愈守护

依赖: pip install pystray pycaw
用法: python launcher.py         (常驻托盘)
      python launcher.py --selftest   (只检测系统配置, 输出结果)
"""
import ctypes
import json
import os
import platform
import queue
import subprocess
import sys
import threading
import time
from ctypes import byref, sizeof
from ctypes import wintypes
from pathlib import Path

if getattr(sys, "frozen", False):
    BASE = Path(sys.executable).resolve().parent
else:
    BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
REPORTS = DATA / "reports"
CONFIG_PATH = BASE / "config.json"
APPS_DETECTED = DATA / "apps_detected.json"
ICON_PATH = BASE / "icon.ico"
LOG_PATH = DATA / "log.txt"
MUTEX_NAME = "QuickLauncher_SingleInstance"

APP_TITLE = "快捷启动台"
APP_VER = "v1.9"
GLOBAL_MODE_NAME = "全局模式"

# 深色科技风主题色板
THEME = {
    "bg":      "#0a0f1c",   # 窗口背景
    "panel":   "#111a2e",   # 面板
    "panel2":  "#0d1526",   # 表格/输入框
    "panel3":  "#182238",   # 悬停
    "line":    "#1d2b47",   # 边框
    "row2":    "#131d33",   # 斑马纹
    "fg":      "#dbe7ff",   # 主文字
    "dim":     "#7d90b2",   # 次要文字
    "accent":  "#22d3ee",   # 主高亮(青)
    "accent2": "#3b82f6",   # 次高亮(蓝)
    "sel":     "#1b3a63",   # 选中行
    "ok":      "#34d399",
    "warn":    "#fbbf24",
}

DEFAULT_CONFIG = {
    "apps": [
        # 在这里添加要快捷启动的应用, 例如:
        # {"name": "微信", "path": "D:\\WeChat\\WeChat.exe"},
    ],
    # 首次启动检测完成后是否已自动填充应用列表(排除系统内置); 只自动填充一次
    "apps_seeded": False,
    "current_mode": "",
    "proxy_server": "127.0.0.1:7890",
    "dns_presets": [
        {"name": "自动获取 (DHCP)", "servers": ["dhcp"]},
        {"name": "阿里 DNS (223.5.5.5)", "servers": ["223.5.5.5", "223.6.6.6"]},
        {"name": "114 DNS", "servers": ["114.114.114.114", "114.114.115.115"]},
        {"name": "Google DNS (8.8.8.8)", "servers": ["8.8.8.8", "8.8.4.4"]},
    ],
    # 自定义模式: 一键切换/启动一组应用。
    # "stop" 填进程映像名(可不填, 默认取 path 的 .exe 文件名), 用于精确停止本次启动的进程。
    # "settings" 可选: 启动模式时自动应用, 例如:
    #   {"volume": 60, "display": {"w": 2560, "h": 1600, "hz": 240}}
    #   volume 0-100; display 为主显示器分辨率/刷新率, 或 {"recommended": true} 恢复推荐。
    "modes": [
        # {"name": "工作模式", "apps": [
        #     {"name": "浏览器", "path": "D:\\\\Program Files\\\\chrome.exe", "stop": "chrome.exe"},
        #     {"name": "编辑器", "path": "D:\\\\tools\\\\Code.exe", "stop": "Code.exe"},
        # ]},
    ],
    # 全局保留应用 (旧写法, 兼容用): 切换模式时不会被停止, 例如 ["WeChat.exe"]
    "global_keep": [],
    # 全局模式: 所有其他模式都会自动包含它的应用, 启动/切换模式时一起启动, 且不会被停止
    "global_mode": {"apps": []},
}


# ---------------------------------------------------------------- 基础工具
def log(msg):
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def log_exc():
    import traceback
    log(traceback.format_exc())


def dec_bytes(b):
    if b is None:
        return ""
    for enc in ("gbk", "utf-8"):
        try:
            return b.decode(enc)
        except (UnicodeDecodeError, AttributeError):
            continue
    return str(b)


CREATE_NO_WINDOW = 0x08000000


def _hidden_kwargs():
    """让子进程不弹出任何控制台窗口 (打包成 exe 后尤其重要)。"""
    kw = {}
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        kw["startupinfo"] = si
        kw["creationflags"] = CREATE_NO_WINDOW
    except Exception:
        pass
    return kw


def run_capture(args, timeout=25):
    """跑命令并取输出, 返回 (rc, stdout, stderr); 全程不弹控制台。"""
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout,
                           **_hidden_kwargs())
        return r.returncode, dec_bytes(r.stdout), dec_bytes(r.stderr)
    except Exception as e:
        log("run failed: %r" % (e,))
        return -1, "", str(e)


def run_detached(args):
    """后台启动一个外部程序, 不弹控制台。"""
    try:
        subprocess.Popen(args, **_hidden_kwargs())
        return True
    except Exception as e:
        log("spawn failed: %r" % (e,))
        return False


def ps(cmd, timeout=30):
    """运行 PowerShell 并返回 stdout 文本。"""
    try:
        rc, so, se = run_capture(
            ["powershell", "-NoProfile", "-Command",
             "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; " + cmd],
            timeout=timeout)
        if rc != 0:
            log("ps failed(%s): %s" % (rc, (se or "").strip()[:200]))
        return (so or "").strip()
    except Exception as e:
        log("ps failed: %r" % (e,))
        return ""


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def com_init():
    """确保当前线程已初始化 COM (comtypes 只会自动初始化第一个用它的线程)。"""
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        pass


def run_smart(cmdline, ok_msg, notify):
    """先直接执行 cmd 命令; 失败则弹出 UAC 提权执行。"""
    try:
        rc, so, se = run_capture(["cmd", "/c", cmdline], timeout=90)
        out = (so + se).strip()
        if rc == 0:
            tail = out.splitlines()[0] if out else ""
            notify(ok_msg + ("\n" + tail if tail else ""))
            return
        log("cmd failed(%d): %s -> %s" % (rc, cmdline, out[:300]))
    except Exception as e:
        log("cmd exception: %r" % (e,))
    r = ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe",
                                            "/c " + cmdline, str(BASE), 0)
    if r > 32:
        notify(ok_msg + "\n已请求管理员权限，请在 UAC 弹窗中确认。")
    else:
        notify("执行失败，请检查权限设置。")


def act(fn):
    """把无参函数包装成 pystray 菜单回调。"""
    return lambda icon, item: fn()


# ---------------------------------------------------------------- 显示 (分辨率/刷新率)
class DEVMODEW(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * 32),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmOrientation", ctypes.c_short),
        ("dmPaperSize", ctypes.c_short),
        ("dmPaperLength", ctypes.c_short),
        ("dmPaperWidth", ctypes.c_short),
        ("dmScale", ctypes.c_short),
        ("dmCopies", ctypes.c_short),
        ("dmDefaultSource", ctypes.c_short),
        ("dmPrintQuality", ctypes.c_short),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", wintypes.WCHAR * 32),
        ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningHeight", wintypes.DWORD),
    ]


class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("DeviceName", wintypes.WCHAR * 32),
        ("DeviceString", wintypes.WCHAR * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", wintypes.WCHAR * 128),
        ("DeviceKey", wintypes.WCHAR * 128),
    ]


user32 = ctypes.windll.user32
user32.EnumDisplaySettingsW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                        ctypes.POINTER(DEVMODEW)]
user32.EnumDisplaySettingsW.restype = wintypes.BOOL
user32.ChangeDisplaySettingsExW.argtypes = [wintypes.LPCWSTR,
                                            ctypes.POINTER(DEVMODEW),
                                            wintypes.HWND, wintypes.DWORD,
                                            wintypes.LPVOID]
user32.ChangeDisplaySettingsExW.restype = wintypes.LONG
user32.EnumDisplayDevicesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                       ctypes.POINTER(DISPLAY_DEVICEW),
                                       wintypes.DWORD]
user32.EnumDisplayDevicesW.restype = wintypes.BOOL

ENUM_CURRENT_SETTINGS = -1
ENUM_REGISTRY_SETTINGS = -2
DISPLAY_DEVICE_ACTIVE = 0x1
CDS_UPDATEREGISTRY = 0x1
DM_BITSPERPEL = 0x40000
DM_PELSWIDTH = 0x80000
DM_PELSHEIGHT = 0x100000
DM_DISPLAYFREQUENCY = 0x400000

CHANGE_ERR = {0: "切换成功", 1: "需要重启系统后生效", -1: "切换失败",
              -2: "显示器不支持该模式", -3: "注册表未更新", -4: "参数错误",
              -5: "参数错误", -6: "多显示器设置失败"}


def enum_displays():
    """返回当前活动的显示器列表 [(DeviceName, DeviceString), ...]"""
    devs = []
    i = 0
    while True:
        d = DISPLAY_DEVICEW()
        d.cb = sizeof(DISPLAY_DEVICEW)
        if not user32.EnumDisplayDevicesW(None, i, byref(d), 0):
            break
        if d.StateFlags & DISPLAY_DEVICE_ACTIVE:
            devs.append((d.DeviceName, d.DeviceString))
        i += 1
    return devs


def enum_modes(name):
    """枚举显示器支持的模式 [(宽, 高, 刷新率, 色深), ...] 去重。"""
    modes = []
    i = 0
    while True:
        dm = DEVMODEW()
        dm.dmSize = sizeof(DEVMODEW)
        if not user32.EnumDisplaySettingsW(name or None, i, byref(dm)):
            break
        key = (dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency,
               dm.dmBitsPerPel)
        if key not in modes:
            modes.append(key)
        i += 1
    return modes


def get_mode(name, which=ENUM_CURRENT_SETTINGS):
    dm = DEVMODEW()
    dm.dmSize = sizeof(DEVMODEW)
    if not user32.EnumDisplaySettingsW(name or None, which, byref(dm)):
        return None
    return (dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency,
            dm.dmBitsPerPel)


def switch_mode(name, w, h, f, b):
    dm = DEVMODEW()
    dm.dmSize = sizeof(DEVMODEW)
    if not user32.EnumDisplaySettingsW(name or None, ENUM_CURRENT_SETTINGS,
                                       byref(dm)):
        return -1
    dm.dmPelsWidth, dm.dmPelsHeight = w, h
    dm.dmDisplayFrequency, dm.dmBitsPerPel = f, b
    dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY | DM_BITSPERPEL
    return user32.ChangeDisplaySettingsExW(name or None, byref(dm), None,
                                           CDS_UPDATEREGISTRY, None)


def reset_display(name):
    """恢复注册表中保存的推荐模式。"""
    return user32.ChangeDisplaySettingsExW(name or None, None, None, 0, None)


def fmt_mode(m):
    return "%d×%d @ %dHz" % (m[0], m[1], m[2])


# ---------------------------------------------------------------- 网络
def get_up_adapters():
    out = ps("Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | "
             "Select-Object -ExpandProperty Name")
    return [l.strip() for l in out.splitlines() if l.strip()]


def get_all_adapters():
    out = ps("Get-NetAdapter | Select-Object -ExpandProperty Name")
    return [l.strip() for l in out.splitlines() if l.strip()]


def wifi_profiles():
    try:
        rc, t, _ = run_capture(["netsh", "wlan", "show", "profiles"], timeout=15)
        names = []
        for line in t.splitlines():
            if ":" in line and ("所有用户配置文件" in line or
                                "All User Profile" in line):
                names.append(line.split(":", 1)[1].strip())
        return names
    except Exception:
        return []


def wifi_ssid():
    try:
        rc, t, _ = run_capture(["netsh", "wlan", "show", "interfaces"], timeout=15)
        for line in t.splitlines():
            if ":" in line and line.strip().upper().startswith("SSID"):
                v = line.split(":", 1)[1].strip()
                if v and not v.startswith("BSSID"):
                    return v
    except Exception:
        pass
    return ""


def _netsh(args, timeout=25):
    """跑一条 netsh, 返回 (返回码, 合并输出文本)。"""
    try:
        rc, so, se = run_capture(["netsh"] + args, timeout=timeout)
        return rc, so + "\n" + se
    except Exception as e:
        log("netsh error %r: %r" % (args, e))
        return 1, ""


def _netsh_arg(a):
    a = str(a).replace('"', "")
    return '"%s"' % a if (" " in a or "\t" in a) else a


def netsh_do(args, ok_msg):
    """执行 netsh 修改命令。直接执行失败(权限不足)时弹 UAC 提权执行。
    返回 (是否成功, 提示文字)。"""
    rc, out = _netsh(args)
    if rc == 0:
        return True, ok_msg
    cmdline = "netsh " + " ".join(_netsh_arg(a) for a in args)
    log("netsh failed(%s): %s -> %s"
        % (rc, cmdline, out.strip().replace("\n", " | ")[:300]))
    try:
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", "cmd.exe", '/c %s' % cmdline, str(BASE), 0)
        if ret > 32:
            return True, ok_msg + " (操作需要管理员权限; 已在 UAC 弹窗中请求提权, 请点“是”完成)"
    except Exception as e:
        log("netsh elevate failed: %r" % (e,))
    return False, "操作失败: " + (out.strip().splitlines()[0] if out.strip() else "未知错误")


def _mask_from_prefix(v):
    """'192.168.1.0/24 (掩码 255.255.255.0)' → '255.255.255.0'。"""
    v = v or ""
    if "(" in v and ")" in v:
        inner = v[v.rfind("(") + 1:v.rfind(")")].strip()
        parts = inner.split()
        if parts:
            return parts[-1].strip()
    return ""


def wlan_ifaces_state():
    """(是否已连接, 当前 SSID, WLAN 接口名)。"""
    rc, t = _netsh(["wlan", "show", "interfaces"])
    state, ssid, ifname = "", "", ""
    for raw in t.splitlines():
        line = raw.strip()
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if not k:
            continue
        if not ifname and (k == "名称" or k == "Name"):
            ifname = v
        elif (not state) and (k == "状态" or k == "State") and "无线电" not in k:
            state = v
        elif k.upper().startswith("SSID") and not k.upper().startswith("BSSID") and v:
            ssid = v
    return (("已连接" in state or state.lower() == "connected"), ssid, ifname)


def wifi_profiles_status():
    """[{'name': 配置名, 'connected': 是否当前连接}] —— 按 netsh 里的保存顺序。"""
    connected, cur_ssid, _ = wlan_ifaces_state()
    rows = []
    rc, t = _netsh(["wlan", "show", "profiles"])
    for raw in t.splitlines():
        if ":" not in raw:
            continue
        k, v = raw.split(":", 1)
        if ("所有用户配置文件" in k) or ("All User Profile" in k):
            nm = v.strip()
            if nm and not nm.startswith("<"):
                rows.append({"name": nm,
                             "connected": bool(connected and cur_ssid and nm == cur_ssid)})
    return rows


def wifi_profile_edit(name):
    """读取某 WiFi 配置: {password, auth, auto, exists}。"""
    d = {"password": "", "auth": "", "auto": True, "exists": False}
    rc, t = _netsh(["wlan", "show", "profile", "name=" + name, "key=clear"])
    d["exists"] = rc == 0 and bool(t.strip())
    for raw in t.splitlines():
        line = raw.strip()
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if ("连接模式" in k) or ("Connection mode" in k):
            low = v.lower()
            if "手动" in v or "manual" in low:
                d["auto"] = False
            elif "自动" in v or "auto" in low:
                d["auto"] = True
        elif ("身份验证" in k) or ("Authentication" in k):
            if not d["auth"] and ("个人" in v or "Personal" in v or "WPA" in v or
                                  "开放" in v or "Open" in v):
                d["auth"] = v
        elif ("关键内容" in k) or ("Key Content" in k):
            d["password"] = v
    return d


def wifi_save_profile(name, password, auto):
    """写回 WiFi 配置: 自动连接(false/true) + 密码(非空才改)。"""
    args = ["wlan", "set", "profileparameter", "name=" + name,
            "ConnectionMode=" + ("auto" if auto else "manual")]
    if password:
        args.append("keyMaterial=" + password)
    return netsh_do(args, 'WiFi 配置「%s」已保存' % name)


def wifi_delete_profile(name):
    return netsh_do(["wlan", "delete", "profile", "name=" + name],
                    '已删除 WiFi 配置「%s」' % name)


def wifi_connect_name(name):
    return netsh_do(["wlan", "connect", "name=" + name], '正在连接 WiFi「%s」' % name)


def wifi_disconnect_now():
    return netsh_do(["wlan", "disconnect"], "已断开 WiFi")


def adapter_list():
    """所有网卡的简要配置(一次 netsh 取全量):
    [{'name','dhcp','ip','mask','gw','connected'}]。"""
    rc, t = _netsh(["interface", "ipv4", "show", "addresses"])
    rc2, t2 = _netsh(["interface", "ipv4", "show", "interfaces"])
    states = {}
    for raw in t2.splitlines():
        parts = raw.split()
        if len(parts) >= 5 and parts[0].isdigit():
            states[" ".join(parts[4:])] = parts[3].strip().lower()
    rows, cur = [], None
    for raw in t.splitlines():
        s = raw.strip()
        if s.startswith("接口 ") and "的配置" in s:
            nm = s[len("接口 "):].split("的配置")[0].strip().strip('"').strip()
            st = states.get(nm, "")
            cur = {"name": nm, "dhcp": None, "ip": "", "mask": "", "gw": "",
                   "connected": st in ("connected", "已连接")}
            rows.append(cur)
        elif cur is not None and ":" in s:
            k, v = s.split(":", 1)
            k, v = k.strip(), v.strip()
            if "DHCP" in k and ("已启用" in k or "Enabled" in k):
                cur["dhcp"] = v.strip() in ("是", "Yes", "yes")
            elif k in ("IP 地址", "IP Address"):
                cur["ip"] = v
            elif k in ("子网前缀", "Subnet prefix"):
                cur["mask"] = _mask_from_prefix(v)
            elif k in ("默认网关", "Default Gateway"):
                cur["gw"] = v
    return rows


def adapter_config(name):
    """单个网卡的 IPv4 配置(含 DNS): {dhcp, ip, mask, gw, dns[]}。"""
    rc, t = _netsh(["interface", "ipv4", "show", "config", "name=" + name])
    d = {"dhcp": None, "ip": "", "mask": "", "gw": "", "dns": []}
    in_dns = False
    for raw in t.splitlines():
        s = raw.strip()
        if not s:
            continue
        if ":" in s:
            k, v = s.split(":", 1)
            k, v = k.strip(), v.strip()
            in_dns = False
            if "DHCP" in k and ("已启用" in k or "Enabled" in k):
                d["dhcp"] = v.strip() in ("是", "Yes", "yes")
            elif k in ("IP 地址", "IP Address"):
                d["ip"] = v
            elif k in ("子网前缀", "Subnet prefix"):
                d["mask"] = _mask_from_prefix(v)
            elif k in ("默认网关", "Default Gateway"):
                d["gw"] = v
            elif ("DNS 服务器" in k) or ("DNS servers" in k):
                d["dns"] = [x.strip() for x in v.replace("；", ",").split(",")
                            if x.strip()]
                in_dns = True
        elif in_dns:
            d["dns"].append(s)
    return d


def adapter_set_static(name, ip, mask, gw, dns1="", dns2=""):
    """设置静态 IPv4 (+静态 DNS)。返回 (ok, msg)。"""
    args = ["interface", "ipv4", "set", "address", "name=" + name,
            "static", ip, mask]
    if gw:
        args.append(gw)
    ok, msg = netsh_do(args, "网卡「%s」已设为静态 IP %s/%s" % (name, ip, mask))
    if ok and dns1:
        netsh_do(["interface", "ipv4", "set", "dnsservers", "name=" + name,
                  "static", dns1, "primary"], "主 DNS: " + dns1)
        if dns2:
            netsh_do(["interface", "ipv4", "add", "dnsservers", "name=" + name,
                      dns2, "index=2"], "备用 DNS: " + dns2)
    return ok, msg


def adapter_set_dhcp(name):
    """IPv4 + DNS 都改回自动获取。"""
    ok, msg = netsh_do(["interface", "ipv4", "set", "address", "name=" + name,
                        "dhcp"], "网卡「%s」IP 已改回 DHCP 自动获取" % name)
    netsh_do(["interface", "ipv4", "set", "dnsservers", "name=" + name, "dhcp"],
             "DNS 已改回自动获取")
    return ok, msg


def adapter_enable(name, on):
    return netsh_do(["interface", "set", "interface", "name=" + name,
                     "admin=" + ("enable" if on else "disable")],
                    "网卡「%s」已%s" % (name, "启用" if on else "禁用"))


def open_windows_setting(uri="ms-settings:network-status"):
    """打开 Windows 设置里的网络页 / 控制面板网络连接。"""
    try:
        if uri.startswith("control:"):
            run_detached(["control.exe", uri.split(":", 1)[1]])
        else:
            ctypes.windll.shell32.ShellExecuteW(None, "open", uri, None, None, 1)
        return True
    except Exception as e:
        log("open setting failed: %r" % (e,))
        return False


def apply_dns(servers, notify):
    """servers == ['dhcp'] 自动获取; 否则设置静态 DNS (第一条为主, 其余为辅)。"""
    ads = get_up_adapters()
    if not ads:
        notify("未发现处于活动状态的网卡。")
        return
    name = ads[0]
    if servers == ["dhcp"]:
        cmds = ['netsh interface ip set dns name="%s" source=dhcp' % name]
    else:
        cmds = ['netsh interface ip set dns name="%s" static %s' % (name, servers[0])]
        for i, s in enumerate(servers[1:], 2):
            cmds.append('netsh interface ip add dns name="%s" %s index=%d'
                        % (name, s, i))
    run_smart(" && ".join(cmds),
              "已切换 %s 的 DNS" % name, notify)


def set_adapter_state(name, enable, notify):
    state = "enable" if enable else "disable"
    run_smart('netsh interface set interface name="%s" admin=%s' % (name, state),
              "网卡 %s 已%s" % (name, "启用" if enable else "禁用"), notify)


def connect_wifi(ssid_name, notify):
    run_smart('netsh wlan connect name="%s"' % ssid_name,
              "正在连接 WiFi: %s" % ssid_name, notify)


def get_proxy():
    import winreg
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        en, _ = winreg.QueryValueEx(k, "ProxyEnable")
        srv, _ = winreg.QueryValueEx(k, "ProxyServer")
        winreg.CloseKey(k)
        return bool(en), srv
    except Exception:
        return False, ""


def set_proxy(enable, server=""):
    import winreg
    k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                       r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
                       0, winreg.KEY_SET_VALUE)
    winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 1 if enable else 0)
    if server:
        winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, server)
    winreg.CloseKey(k)
    wininet = ctypes.windll.wininet
    wininet.InternetSetOptionW(0, 39, 0, 0)   # INTERNET_OPTION_SETTINGS_CHANGED
    wininet.InternetSetOptionW(0, 37, 0, 0)   # INTERNET_OPTION_REFRESH


def net_status_text():
    lines = []
    ssid = wifi_ssid()
    if ssid:
        lines.append("WiFi: %s" % ssid)
    ads = get_up_adapters()
    lines.append("活动网卡: %s" % (", ".join(ads) if ads else "无"))
    ip = ps("Get-NetIPAddress -AddressFamily IPv4 | Where-Object "
            "{$_.IPAddress -notlike '169.254*' -and $_.IPAddress -ne '127.0.0.1'} | "
            "ForEach-Object { $_.IPAddress + ' (' + $_.InterfaceAlias + ')' }")
    if ip:
        lines.append("IPv4: " + " | ".join(ip.splitlines()))
    dns = ps("Get-DnsClientServerAddress -AddressFamily IPv4 | Where-Object "
             "{$_.ServerAddresses} | ForEach-Object { $_.InterfaceAlias + '=' + "
             "($_.ServerAddresses -join ',') }")
    if dns:
        lines.append("DNS: " + " | ".join(dns.splitlines()))
    en, srv = get_proxy()
    lines.append("代理: %s %s" % ("开启" if en else "关闭",
                                 srv if (en and srv) else ""))
    return "\n".join(lines)


# ---------------------------------------------------------------- 音量
class Volume:
    def __init__(self):
        self._v = None
        try:
            com_init()
            from pycaw.pycaw import AudioUtilities
            dev = AudioUtilities.GetSpeakers()
            if hasattr(dev, "EndpointVolume"):
                # pycaw 2026+ (AudioDevice)
                self._v = dev.EndpointVolume
            else:
                # pycaw 0.3.x (IMMDevice)
                from comtypes import CLSCTX_ALL, POINTER, cast
                from pycaw.pycaw import IAudioEndpointVolume
                iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                self._v = cast(iface, POINTER(IAudioEndpointVolume))
        except Exception as e:
            log("volume init failed: %r" % (e,))

    def available(self):
        return self._v is not None

    def get(self):
        return self._v.GetMasterVolumeLevelScalar()

    def set(self, x):
        self._v.SetMasterVolumeLevelScalar(x, None)

    def muted(self):
        return bool(self._v.GetMute())

    def set_mute(self, m):
        self._v.SetMute(int(m), None)


def current_volume():
    """在当前线程新建一个可用 Volume (COM 对象不能跨线程直接用)。"""
    v = Volume()
    return v if v.available() else None


# ---------------------------------------------------------------- 应用检测
def lnk_dirs():
    dirs = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        dirs.append(Path(appdata) / "Microsoft/Windows/Start Menu/Programs")
    pd = os.environ.get("ProgramData")
    if pd:
        dirs.append(Path(pd) / "Microsoft/Windows/Start Menu/Programs")
    return dirs


def is_system_app(path):
    """系统内置应用判定(排除项): Windows 目录下、Store(WindowsApps)、空路径。"""
    p = (path or "").replace("/", "\\").lower()
    if not p:
        return True
    root = (os.environ.get("SystemRoot") or r"C:\Windows").replace("/", "\\").lower()
    if p == root or p.startswith(root + "\\"):
        return True
    if "windowsapps" in p:
        return True
    return False


# ---------- 应用图标提取 (SHGetFileInfoW -> HICON -> GDI DIB -> PIL Image)
class _ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", ctypes.c_int),
                ("xHotspot", ctypes.c_ulong),
                ("yHotspot", ctypes.c_ulong),
                ("hbmMask", ctypes.c_void_p),
                ("hbmColor", ctypes.c_void_p)]


class _BITMAP(ctypes.Structure):
    _fields_ = [("bmType", ctypes.c_long),
                ("bmWidth", ctypes.c_long),
                ("bmHeight", ctypes.c_long),
                ("bmWidthBytes", ctypes.c_long),
                ("bmPlanes", ctypes.c_ushort),
                ("bmBitsPixel", ctypes.c_ushort),
                ("bmBits", ctypes.c_void_p)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32),
                ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32),
                ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16),
                ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32),
                ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32),
                ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER),
                ("bmiColors", ctypes.c_uint32 * 3)]


class _SHFILEINFOW(ctypes.Structure):
    _fields_ = [("hIcon", ctypes.c_void_p),
                ("iIcon", ctypes.c_int),
                ("dwAttributes", ctypes.c_uint32),
                ("szDisplayName", ctypes.c_wchar * 260),
                ("szTypeName", ctypes.c_wchar * 80)]


def _hicon_to_image(hicon):
    """HICON -> RGBA PIL.Image (32 位 DIB); 失败返回 None。"""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    # 64 位下句柄返回值必须显式声明, 否则被截断成 32 位
    user32.GetDC.restype = ctypes.c_void_p
    user32.GetIconInfo.restype = ctypes.c_int
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.GetObjectW.restype = ctypes.c_int
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteDC.restype = ctypes.c_int
    gdi32.DeleteObject.restype = ctypes.c_int
    user32.ReleaseDC.restype = ctypes.c_int
    user32.DestroyIcon.restype = ctypes.c_int
    ii = _ICONINFO()
    memdc = None
    hdc = None
    old = None

    def H(x):   # 64 位句柄转无符号 c_void_p, 防止负值截断
        return ctypes.c_void_p(x & 0xFFFFFFFFFFFFFFFF)

    try:
        if not user32.GetIconInfo(H(hicon), ctypes.byref(ii)):
            return None
        hbm = ii.hbmColor or ii.hbmMask
        if not hbm:
            return None
        hbm = H(hbm)
        bm = _BITMAP()
        if not gdi32.GetObjectW(hbm, ctypes.sizeof(bm), ctypes.byref(bm)):
            return None
        w, h = bm.bmWidth, bm.bmHeight
        if w <= 0 or h <= 0:
            return None
        hdc = user32.GetDC(0)
        memdc = gdi32.CreateCompatibleDC(H(hdc))
        old = gdi32.SelectObject(H(memdc), hbm)
        bih = _BITMAPINFOHEADER()
        bih.biSize = ctypes.sizeof(bih)
        bih.biWidth = w
        bih.biHeight = -h          # 负值 = 自顶向下
        bih.biPlanes = 1
        bih.biBitCount = 32
        bih.biCompression = 0      # BI_RGB
        bi = _BITMAPINFO()
        bi.bmiHeader = bih
        buf = ctypes.create_string_buffer(w * h * 4)
        ok = gdi32.GetDIBits(H(memdc), hbm, 0, h, buf, ctypes.byref(bi), 0)
        if not ok:
            return None
        from PIL import Image
        return Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1)
    except Exception:
        return None
    finally:
        if memdc:
            gdi32.SelectObject(H(memdc), old)
            gdi32.DeleteDC(H(memdc))
        if hdc:
            user32.ReleaseDC(0, H(hdc))
        if ii.hbmColor:
            gdi32.DeleteObject(H(ii.hbmColor))
        if ii.hbmMask:
            gdi32.DeleteObject(H(ii.hbmMask))
        if hicon:
            user32.DestroyIcon(H(hicon))


def extract_icon(path, size=32):
    """提取文件关联的大图标, 返回 RGBA PIL.Image; 失败返回 None。"""
    try:
        shfi = _SHFILEINFOW()
        flags = 0x100 | (0x0 if size >= 32 else 0x1)   # SHGFI_ICON | SHGFI_LARGEICON
        if not ctypes.windll.shell32.SHGetFileInfoW(
                path, 0, ctypes.byref(shfi), ctypes.sizeof(shfi), flags):
            return None
        if not shfi.hIcon:
            return None
        return _hicon_to_image(shfi.hIcon)
    except Exception:
        return None


ICON_DIR = BASE / "data" / "icons"


def icon_cache_path(path):
    """应用路径 -> data/icons/<md5>.png 缓存文件路径。"""
    try:
        import hashlib
        h = hashlib.md5((path or "").lower().encode("utf-8", "ignore")).hexdigest()[:20]
        return ICON_DIR / (h + ".png")
    except Exception:
        return None


def load_cached_icon(path):
    """从磁盘缓存读图标 (快); 没有则 None。"""
    try:
        p = icon_cache_path(path)
        if p is not None and p.is_file():
            from PIL import Image
            return Image.open(str(p)).convert("RGBA")
    except Exception:
        pass
    return None


def save_cached_icon(path, img):
    """把提取到的图标写进磁盘缓存, 下次启动直接读。"""
    try:
        if img is None:
            return
        ICON_DIR.mkdir(parents=True, exist_ok=True)
        p = icon_cache_path(path)
        if p is not None:
            img.save(str(p), "PNG")
    except Exception:
        pass


def make_letter_icon(text, size=32):
    """提取失败时的兜底图标: 圆角方块 + 首字符。"""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([1, 1, size - 2, size - 2], radius=size // 5,
                        fill=(56, 112, 190, 255))
    ch = (str(text or "").strip()[:1].upper()) or "?"
    font = None
    for fp in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/arial.ttf"):
        try:
            font = ImageFont.truetype(fp, int(size * 0.55))
            break
        except Exception:
            pass
    d.text((size // 2, size // 2), ch, font=font, fill=(255, 255, 255, 255), anchor="mm")
    return img


def detect_apps():
    """扫描开始菜单快捷方式, 返回 (应用列表, 快捷方式总数)。"""
    found = {}
    total = 0
    try:
        com_init()
        from comtypes.client import CreateObject, dynamic
        sh = dynamic.Dispatch(CreateObject("WScript.Shell"))
    except Exception as e:
        log("WScript.Shell failed: %r" % (e,))
        return [], 0
    for root in lnk_dirs():
        if not root.exists():
            continue
        for p in root.rglob("*.lnk"):
            low = p.as_posix().lower()
            if "uninstall" in low or "卸载" in str(p):
                continue
            try:
                target = sh.CreateShortcut(str(p)).TargetPath
            except Exception:
                continue
            if not target or not target.lower().endswith(".exe"):
                continue
            t = Path(target)
            if not t.exists():
                continue
            total += 1
            name = t.stem
            lname = name.lower()
            if "uninstall" in lname or "setup" == lname or "update" in lname:
                continue
            if is_system_app(str(t)):
                continue
            if name not in found:
                found[name] = str(t)
    items = [{"name": n, "path": p} for n, p in sorted(found.items())]
    return items, total


# ---------------------------------------------------------------- 检测报告
def collect_system():
    info = {}
    info["os"] = "%s / %s" % (platform.platform(),
                              ps("[System.Environment]::OSVersion.VersionString"))
    info["machine"] = os.environ.get("COMPUTERNAME", "")
    info["cpu"] = ps("Get-CimInstance Win32_Processor | Select-Object -ExpandProperty Name") or "未知"
    gpu = ps("Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name")
    names = [x.strip() for x in gpu.replace("\r", "").splitlines() if x.strip()]
    real = [n for n in names if "virtual" not in n.lower()]
    info["gpu"] = " / ".join(real or names) if (real or names) else "未知"
    try:
        ram = ps("(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory")
        info["ram"] = "%.1f GB" % (int(ram) / 1073741824.0) if ram.strip().isdigit() else ram or "未知"
    except Exception:
        info["ram"] = "未知"
    return info



def _reg_val(key, sub, name):
    try:
        import winreg
        with winreg.OpenKey(key, sub) as k:
            v, _ = winreg.QueryValueEx(k, name)
            return v
    except Exception:
        return None


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def memory_info():
    try:
        m = _MEMORYSTATUSEX()
        m.dwLength = sizeof(m)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(byref(m)):
            return None
        return {"total": m.ullTotalPhys, "avail": m.ullAvailPhys,
                "load": m.dwMemoryLoad}
    except Exception:
        return None


def storage_info():
    drives = []
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:
        mask = 0
    for i in range(26):
        if not (mask >> i) & 1:
            continue
        letter = chr(65 + i) + ":\\"
        try:
            t = ctypes.windll.kernel32.GetDriveTypeW(letter)
            free = ctypes.c_ulonglong(0)
            total = ctypes.c_ulonglong(0)
            total_free = ctypes.c_ulonglong(0)
            if not ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                    letter, byref(free), byref(total), byref(total_free)):
                continue
            label = ""
            try:
                buf = ctypes.create_unicode_buffer(256)
                if ctypes.windll.kernel32.GetVolumeInformationW(
                        letter, buf, 256, None, None, None, None, 0):
                    label = buf.value
            except Exception:
                pass
            drives.append({"letter": letter, "label": label, "type": t,
                           "total": total.value, "free": total_free.value})
        except Exception:
            continue
    return drives


def cpu_info():
    import winreg
    name = _reg_val(winreg.HKEY_LOCAL_MACHINE,
                    r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
                    "ProcessorNameString")
    mhz = _reg_val(winreg.HKEY_LOCAL_MACHINE,
                   r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "~MHz")
    return {"name": name or "未知", "mhz": mhz, "logical": os.cpu_count() or 0}


def gpu_info():
    rows = []
    try:
        out = ps("Get-CimInstance Win32_VideoController | ForEach-Object { "
                 "\"$($_.Name)|$($_.AdapterRAM)|$($_.DriverVersion)\" }")
        for line in out.splitlines():
            line = line.strip()
            if not line or "virtual" in line.lower():
                continue
            parts = line.split("|")
            name = parts[0].strip() if parts and parts[0].strip() else "未知"
            # 注: Win32_VideoController.AdapterRAM 是 32 位值, >4GB 显存会截断失真,
            # 故不展示显存大小, 只给名称 + 驱动版本。
            drv = parts[2].strip() if len(parts) > 2 else ""
            rows.append({"name": name, "driver": drv})
    except Exception:
        pass
    return rows


def os_info():
    import winreg
    k = winreg.HKEY_LOCAL_MACHINE
    base = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
    osname = (_reg_val(k, base, "ProductName") or platform.system())
    disp = _reg_val(k, base, "DisplayVersion") or ""
    build = _reg_val(k, base, "CurrentBuild") or ""
    return {"name": osname, "display": disp, "build": build,
            "arch": platform.machine(),
            "node": os.environ.get("COMPUTERNAME", ""),
            "user": os.environ.get("USERNAME", "")}


# ---- WiFi 软件无线电开关 (WLAN API, 无需管理员) ----
class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


class _WLAN_INTERFACE_INFO(ctypes.Structure):
    _fields_ = [("InterfaceGuid", _GUID), ("strInterfaceDescription", wintypes.LPWSTR),
                ("isState", wintypes.DWORD)]


class _WLAN_INTERFACE_INFO_LIST(ctypes.Structure):
    _fields_ = [("dwNumberOfItems", wintypes.DWORD), ("dwIndex", wintypes.DWORD),
                ("InterfaceInfo", _WLAN_INTERFACE_INFO * 1)]


class _WLAN_PHY_RADIO_STATE(ctypes.Structure):
    _fields_ = [("dwPhyIndex", wintypes.DWORD),
                ("dot11SoftwareRadioState", wintypes.DWORD),
                ("dot11HardwareRadioState", wintypes.DWORD)]


_wlan = None


def _wlan_dll():
    global _wlan
    if _wlan is None:
        _wlan = ctypes.WinDLL("wlanapi.dll")
        _wlan.WlanOpenHandle.argtypes = [wintypes.DWORD, wintypes.LPVOID,
                                         ctypes.POINTER(wintypes.DWORD),
                                         ctypes.POINTER(wintypes.HANDLE)]
        _wlan.WlanOpenHandle.restype = wintypes.DWORD
        _wlan.WlanCloseHandle.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
        _wlan.WlanCloseHandle.restype = wintypes.DWORD
        _wlan.WlanEnumInterfaces.argtypes = [wintypes.HANDLE, wintypes.LPVOID,
                                             ctypes.POINTER(ctypes.POINTER(_WLAN_INTERFACE_INFO_LIST))]
        _wlan.WlanEnumInterfaces.restype = wintypes.DWORD
        _wlan.WlanFreeMemory.argtypes = [wintypes.LPVOID]
        _wlan.WlanFreeMemory.restype = wintypes.DWORD
        _wlan.WlanQueryInterface.argtypes = [wintypes.HANDLE, ctypes.POINTER(_GUID),
                                             wintypes.DWORD, wintypes.LPVOID,
                                             ctypes.POINTER(wintypes.DWORD),
                                             ctypes.POINTER(wintypes.LPVOID),
                                             ctypes.POINTER(wintypes.DWORD)]
        _wlan.WlanQueryInterface.restype = wintypes.DWORD
        _wlan.WlanSetInterface.argtypes = [wintypes.HANDLE, ctypes.POINTER(_GUID),
                                           wintypes.DWORD, wintypes.DWORD,
                                           wintypes.LPVOID, wintypes.LPVOID]
        _wlan.WlanSetInterface.restype = wintypes.DWORD
    return _wlan


def _wlan_handle():
    """打开 WLAN 句柄并返回第一个无线接口的 GUID。失败返回 (None, None)。"""
    try:
        w = _wlan_dll()
        h = wintypes.HANDLE()
        ver = wintypes.DWORD()
        if w.WlanOpenHandle(2, None, ctypes.byref(ver), ctypes.byref(h)) != 0:
            return None, None
        plist = ctypes.POINTER(_WLAN_INTERFACE_INFO_LIST)()
        if w.WlanEnumInterfaces(h, None, ctypes.byref(plist)) != 0 or not plist:
            w.WlanCloseHandle(h, None)
            return None, None
        if plist.contents.dwNumberOfItems == 0:
            w.WlanFreeMemory(plist)
            w.WlanCloseHandle(h, None)
            return None, None
        g = _GUID()
        ctypes.memmove(ctypes.byref(g),
                       ctypes.byref(plist.contents.InterfaceInfo[0].InterfaceGuid),
                       ctypes.sizeof(_GUID))
        w.WlanFreeMemory(plist)
        return h, g
    except Exception:
        return None, None


def wifi_adapter_name():
    """返回 WiFi 无线适配器名 (通常 'WLAN')。"""
    try:
        _, _, ifname = wlan_ifaces_state()
        if ifname:
            return ifname
    except Exception:
        pass
    return "WLAN"


def wifi_radio_on():
    """WiFi 是否开启 (按 WLAN 适配器的管理员状态)。True=开, False=关, None=未知。"""
    name = wifi_adapter_name()
    rc, t = _netsh(["interface", "show", "interface"])
    if rc != 0:
        return None
    for raw in t.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        if " ".join(parts[3:]) == name:
            admin = parts[0].strip()
            if "启用" in admin or admin.lower() == "enabled":
                return True
            if "禁用" in admin or admin.lower() == "disabled":
                return False
    return None


def wifi_set_radio(on):
    """开关 WiFi (禁用/启用 WLAN 适配器)。返回是否成功。"""
    ok, _ = adapter_enable(wifi_adapter_name(), on)
    return ok


def _disk_snapshot():
    """各 PID 的磁盘 I/O 速率(字节/秒), {pid: bytes_per_sec}。"""
    out = ps('Get-CimInstance Win32_PerfFormattedData_PerfProc_Process | '
             'Where-Object { $_.IDProcess -gt 4 } | '
             'ForEach-Object { "$($_.IDProcess)|$($_.IOReadBytesPersec)|$($_.IOWriteBytesPersec)" }')
    res = {}
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        p = line.split("|")
        if len(p) < 3:
            continue
        try:
            pid = int(p[0].strip())
            r = float(p[1].strip() or "0")
            w = float(p[2].strip() or "0")
        except Exception:
            continue
        res[pid] = r + w
    return res


def _conn_snapshot():
    """各 PID 的 TCP 连接数, {pid: count}。"""
    out = ps('Get-NetTCPConnection -ErrorAction SilentlyContinue | '
             'Group-Object OwningProcess | '
             'ForEach-Object { "$($_.Name)|$($_.Count)" }')
    res = {}
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        p = line.split("|")
        if len(p) < 2:
            continue
        try:
            pid = int(p[0].strip())
            cnt = int(p[1].strip())
        except Exception:
            continue
        res[pid] = cnt
    return res


def _gpu_snapshot():
    """各 PID 的 GPU 使用率(%), {pid: sm%}; 无 nvidia-smi 时返回 {}。"""
    res = {}
    try:
        rc, out, _ = run_capture(["nvidia-smi", "pmon", "-c", "1", "-s", "u"],
                                 timeout=25)
    except Exception:
        return res
    if rc != 0:
        return res
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        p = line.split()
        if len(p) < 4:
            continue
        try:
            pid = int(p[1])
            sm = float(p[3])
        except Exception:
            continue
        res[pid] = sm
    return res


def _fmt_rate(bps):
    bps = max(0.0, bps or 0.0)
    if bps >= 1048576.0:
        return "%.1f MB/s" % (bps / 1048576.0)
    if bps >= 1024.0:
        return "%.0f KB/s" % (bps / 1024.0)
    return "%.0f B/s" % bps


def _net_stats():
    """本机所有网卡的累计收/发字节。返回 (recv_bytes, sent_bytes)。"""
    try:
        rc, out, _ = run_capture(["powershell", "-NoProfile", "-Command",
            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
            "Get-NetAdapterStatistics | ForEach-Object { '{0}|{1}' -f $_.ReceivedBytes, $_.SentBytes }"],
            timeout=30)
    except Exception:
        return 0, 0
    if rc != 0:
        return 0, 0
    recv = sent = 0
    for line in out.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        a, _, b = line.partition("|")
        try:
            recv += int(a.strip() or "0")
        except Exception:
            pass
        try:
            sent += int(b.strip() or "0")
        except Exception:
            pass
    return recv, sent


def _proc_snapshot():
    """一次取全量进程的 CPU(累计秒) 与内存(字节)。返回 {映像名小写: {count,cpu,mem}}。"""
    out = ps("Get-Process | ForEach-Object { "
             "\"$($_.ProcessName)|$($_.Id)|$($_.CPU)|$($_.WorkingSet64)\" }")
    data = {}
    pid2name = {}
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) < 4:
            continue
        name = parts[0].strip().lower()
        if not name:
            continue
        try:
            pid = int(parts[1].strip())
        except Exception:
            pid = 0
        try:
            cpu = float(parts[2].strip() or "0")
        except Exception:
            cpu = 0.0
        try:
            ws = int(parts[3].strip() or "0")
        except Exception:
            ws = 0
        d = data.setdefault(name, {"count": 0, "cpu": 0.0, "mem": 0})
        d["count"] += 1
        d["cpu"] += cpu
        d["mem"] += ws
        if pid:
            pid2name[pid] = name
    return data, pid2name


def _img_key(img):
    """把映像名归一成小写、去掉 .exe/.bat/.cmd 后缀。"""
    img = (img or "").strip().lower()
    for ext in (".exe", ".bat", ".cmd"):
        if img.endswith(ext):
            return img[:-len(ext)]
    return img


def mode_resource_usage(entries):
    """统计给定应用条目(列表)的资源占用。两次采样估算 CPU% 与整机网络速率。
    返回 {"rows":[{name,image,running,count,mem_mb,cpu_pct,disk_bps,gpu_pct}], "down_bps":, "up_bps":}。"""
    images = {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        img = entry_image(e)
        if img:
            images.setdefault(_img_key(img), e.get("name") or img)
    if not images:
        return {"rows": [], "down_bps": 0.0, "up_bps": 0.0}
    logical = (cpu_info().get("logical") or 1)
    t0 = time.time()
    net0 = _net_stats()
    s1, pid2name = _proc_snapshot()
    disk = _disk_snapshot()
    gpu = _gpu_snapshot()
    s2, pid2name2 = _proc_snapshot()
    net1 = _net_stats()
    pid2name.update(pid2name2)
    elapsed = max(time.time() - t0, 0.5)
    agg = {}
    for pid, nm in pid2name.items():
        a = agg.setdefault(nm, {"disk": 0.0, "gpu": 0.0})
        a["disk"] += disk.get(pid, 0.0)
        a["gpu"] += gpu.get(pid, 0.0)
    rows = []
    for key in images:
        a = s1.get(key, {"count": 0, "cpu": 0.0, "mem": 0})
        b = s2.get(key, {"count": 0, "cpu": 0.0, "mem": 0})
        dcpu = b["cpu"] - a["cpu"]
        extra = agg.get(key, {"disk": 0.0, "gpu": 0.0})
        rows.append({
            "name": images[key],
            "image": key,
            "running": b["count"] > 0,
            "count": b["count"],
            "mem_mb": b["mem"] / 1048576.0,
            "cpu_pct": dcpu / elapsed / logical * 100.0,
            "disk_bps": extra["disk"],
            "gpu_pct": extra["gpu"],
        })
    rows.sort(key=lambda r: (-r["mem_mb"], -r["cpu_pct"]))
    return {"rows": rows,
            "down_bps": max(0.0, (net1[0] - net0[0]) / elapsed),
            "up_bps": max(0.0, (net1[1] - net0[1]) / elapsed)}


def collect_system_detailed():
    return {"os": os_info(), "cpu": cpu_info(), "gpu": gpu_info(),
            "mem": memory_info(), "disks": storage_info(),
            "py": platform.python_version()}


def _gb(n):
    return "%.1f GB" % (n / 1073741824.0)


def format_system_info(d):
    L = []
    osd = d.get("os") or {}
    L.append("【操作系统】")
    L.append("  系统: %s %s (Build %s)" % (osd.get("name") or "?",
                                          osd.get("display") or "",
                                          osd.get("build") or ""))
    L.append("  架构: %s    计算机名: %s    用户: %s"
             % (osd.get("arch") or "?", osd.get("node") or "?",
                osd.get("user") or "?"))
    c = d.get("cpu") or {}
    L.append("")
    L.append("【处理器】")
    L.append("  %s" % (c.get("name") or "?"))
    L.append("  逻辑核心: %d    频率: %s MHz" % (c.get("logical") or 0,
                                                 c.get("mhz") or "?"))
    L.append("")
    L.append("【显卡】")
    gpus = d.get("gpu") or []
    if gpus:
        for g in gpus:
            L.append("  %s%s" % (g.get("name") or "?",
                                (" · 驱动 " + g["driver"]) if g.get("driver") else ""))
    else:
        L.append("  (未获取到)")
    m = d.get("mem")
    L.append("")
    L.append("【内存】")
    if m:
        L.append("  总内存: %s    可用: %s    使用率: %d%%"
                 % (_gb(m["total"]), _gb(m["avail"]), m["load"]))
    else:
        L.append("  (未获取到)")
    L.append("")
    L.append("【存储】")
    disks = d.get("disks") or []
    if disks:
        for s in disks:
            tt = {2: "可移动", 3: "本地磁盘", 4: "网络", 5: "光驱",
                  6: "RAM 盘"}.get(s["type"], "磁盘")
            L.append("  %s [%s] %s    共 %s    可用 %s"
                     % (s["letter"], tt, s["label"] or "",
                        _gb(s["total"]), _gb(s["free"])))
    else:
        L.append("  (未获取到)")
    L.append("")
    L.append("【运行环境】")
    L.append("  Python %s · %s" % (d.get("py") or "?", platform.platform()))
    return "\n".join(L)


def _xml_escape(v):
    return (str(v).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;"))


def wifi_scan_networks():
    """扫描附近可见 WiFi, 只返回未保存的网络, 按信号降序:
    [{'ssid','signal','auth','band'}]。"""
    saved = {r["name"] for r in wifi_profiles_status()}
    rc, t = _netsh(["wlan", "show", "networks", "mode=bssid"])
    nets, cur = [], None
    for raw in t.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("SSID ") and ":" in line:
            nm = line.split(":", 1)[1].strip()
            if cur and cur.get("ssid"):
                nets.append(cur)
            cur = {"ssid": nm, "signal": None, "auth": "", "band": "",
                   "channel": None, "radio": ""}
        elif cur is not None and ":" in line:
            k, v = line.split(":", 1)
            k, v = k.strip(), v.strip()
            if k in ("身份验证", "Authentication"):
                if not cur["auth"]:
                    cur["auth"] = v
            elif k in ("加密", "Encryption"):
                if not cur["auth"]:
                    cur["auth"] = v
            elif k in ("信号", "Signal"):
                try:
                    sig = int(v.replace("%", "").strip())
                except Exception:
                    sig = None
                if sig is not None and (cur["signal"] is None or sig > cur["signal"]):
                    cur["signal"] = sig
            elif k in ("通道", "Channel"):
                if not cur["channel"]:
                    try:
                        cur["channel"] = int(v.strip())
                    except Exception:
                        pass
            elif k in ("无线电类型", "Radio type"):
                if not cur["radio"]:
                    cur["radio"] = v
    if cur and cur.get("ssid"):
        nets.append(cur)

    out, seen = [], set()
    for n in nets:
        name = n["ssid"]
        if not name or name in seen:
            continue
        seen.add(name)
        if name in saved:
            continue
        ch = n.get("channel")
        if ch:
            band = ("5G" if ch > 14 else "2.4G") + (" · ch%s" % ch)
        else:
            band = n.get("radio") or ""
        out.append({"ssid": name, "signal": n.get("signal"),
                    "auth": n.get("auth") or "开放", "band": band})
    out.sort(key=lambda x: -(x.get("signal") or 0))
    return out


def wifi_connect_new(ssid, password="", auth=""):
    """给未保存的网络生成配置文件并连接(开放网络无需密码)。"""
    sec = ""
    if auth and ("开放" not in auth and "Open" not in auth):
        aut, enc = "WPA2PSK", "AES"
        if "WPA3" in auth.upper() or "SAE" in auth.upper():
            aut, enc = "WPA3SAE", "AES"
        sec = ('<security><authEncryption><authentication>%s</authentication>'
               '<encryption>%s</encryption><useOneX>false</useOneX></authEncryption>'
               '<sharedKey><keyType>passPhrase</keyType><protected>false</protected>'
               '<keyMaterial>%s</keyMaterial></sharedKey></security>'
               % (aut, enc, _xml_escape(password)))
    xml = ('<?xml version="1.0"?>\n'
           '<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">\n'
           '  <name>%s</name>\n'
           '  <SSIDConfig><SSID><name>%s</name></SSID></SSIDConfig>\n'
           '  <connectionType>ESS</connectionType>\n'
           '  <connectionMode>manual</connectionMode>\n'
           '  <MSM>%s</MSM>\n'
           '</WLANProfile>\n' % (_xml_escape(ssid), _xml_escape(ssid), sec))
    path = os.path.join(str(DATA), "_wlan_new.xml")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(xml)
    except Exception as e:
        return False, "写配置失败: %r" % (e,)
    ok, msg = netsh_do(["wlan", "add", "profile", "filename=" + path],
                       "已添加 WiFi「%s」" % ssid)
    if not ok:
        return ok, msg
    return netsh_do(["wlan", "connect", "name=" + ssid, "ssid=" + ssid],
                    "正在连接「%s」" % ssid)


def do_detection():
    """检测本机系统配置, 写报告, 返回 (报告路径, 摘要文本)。"""
    com_init()
    sys_info = collect_system()

    displays = []
    for name, label in enum_displays():
        cur = get_mode(name)
        rec = get_mode(name, ENUM_REGISTRY_SETTINGS)
        modes = enum_modes(name)
        displays.append({"name": name, "label": label, "current": cur,
                         "recommended": rec, "modes": modes})
    if not displays:
        cur = get_mode(None)
        rec = get_mode(None, ENUM_REGISTRY_SETTINGS)
        modes = enum_modes(None)
        displays.append({"name": "", "label": "主显示器", "current": cur,
                         "recommended": rec, "modes": modes})

    vol = Volume()
    vol_text = ("%d%%" % int(vol.get() * 100)) if vol.available() else "无音频设备"

    en, srv = get_proxy()
    ssid = wifi_ssid()
    ads = get_up_adapters()
    apps, app_total = detect_apps()

    # ---- 报告
    REPORTS.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS / ("检测报告_%s.txt" % time.strftime("%Y%m%d_%H%M%S"))
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("=" * 60 + "\n")
        f.write("快捷启动台 · 系统配置检测报告\n")
        f.write("时间: %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        f.write("=" * 60 + "\n\n")
        f.write("【系统】\n")
        f.write("  操作系统: %s\n" % sys_info["os"])
        f.write("  计算机名: %s\n" % sys_info["machine"])
        f.write("  CPU: %s\n" % sys_info["cpu"])
        f.write("  GPU: %s\n" % sys_info["gpu"])
        f.write("  内存: %s\n" % sys_info["ram"])
        f.write("\n【显示】\n")
        for d in displays:
            f.write("  %s (%s)\n" % (d["label"], d["name"] or "主显示器"))
            f.write("    当前模式: %s (色深 %d 位)\n" % (fmt_mode(d["current"]), d["current"][3]) if d["current"] else "    当前模式: 未知\n")
            f.write("    推荐模式: %s\n" % fmt_mode(d["recommended"]) if d["recommended"] else "    推荐模式: 未知\n")
            modes = sorted(d["modes"], key=lambda m: (-(m[0] * m[1]), -m[2]))
            f.write("    支持模式: %d 种 —— %s\n" % (len(modes), ", ".join(fmt_mode(m) for m in modes[:16])))
        f.write("\n【音量】 %s\n" % vol_text)
        f.write("\n【网络】\n")
        f.write("  WiFi: %s\n" % (ssid or "未连接"))
        f.write("  活动网卡: %s\n" % (", ".join(ads) if ads else "无"))
        f.write("  代理: %s %s\n" % ("开启" if en else "关闭", srv if (en and srv) else ""))
        f.write("\n【已安装应用】 共检测到 %d 个应用 (开始菜单, 已排除系统内置)\n" % len(apps))
        for i, a in enumerate(apps[:60], 1):
            f.write("  %2d. %s  ->  %s\n" % (i, a["name"], a["path"]))
        if len(apps) > 60:
            f.write("  ... 其余 %d 个见 apps_detected.json\n" % (len(apps) - 60))

    # ---- 检测到的应用列表
    with open(APPS_DETECTED, "w", encoding="utf-8") as f:
        json.dump(apps, f, ensure_ascii=False, indent=2)

    # ---- 摘要
    cur_disp = displays[0]
    summary_lines = [
        "系统: %s · GPU %s" % (platform.release(), sys_info["gpu"][:30]),
        "显示: %s (推荐 %s)" % (fmt_mode(cur_disp["current"]) if cur_disp["current"] else "未知",
                               fmt_mode(cur_disp["recommended"]) if cur_disp["recommended"] else "未知"),
        "音量: %s · WiFi: %s" % (vol_text, ssid or "未连接"),
        "代理: %s · 检测到 %d 个应用" % ("开启" if en else "关闭", len(apps)),
    ]
    return report_path, "\n".join(summary_lines)


# ---------------------------------------------------------------- 快捷方式
def autostart_lnk():
    appdata = os.environ.get("APPDATA", "")
    return Path(appdata) / "Microsoft/Windows/Start Menu/Programs/Startup" / "快捷启动台.lnk"


def create_shortcut(path, target, args="", workdir="", icon="", description=""):
    com_init()
    from comtypes.client import CreateObject, dynamic
    sh = dynamic.Dispatch(CreateObject("WScript.Shell"))
    lnk = sh.CreateShortcut(str(path))
    lnk.TargetPath = target
    if args:
        lnk.Arguments = args
    if workdir:
        lnk.WorkingDirectory = workdir
    if icon:
        lnk.IconLocation = str(icon) + ",0"
    if description:
        lnk.Description = description
    lnk.Save()


def pythonw_path():
    p = Path(sys.executable).with_name("pythonw.exe")
    return str(p) if p.exists() else sys.executable


def launch_target():
    """开机自启 / 提权重启的启动目标。

    打包成 exe 后目标就是 exe 自身; 源码运行时是 pythonw.exe + launcher.py。
    返回 (可执行文件, 参数字符串)。
    """
    if getattr(sys, "frozen", False):
        return sys.executable, ""
    return pythonw_path(), '"%s"' % Path(__file__).resolve()


def launch_icon():
    """开机自启快捷方式用的图标: 打包后用 exe 自带图标。"""
    return sys.executable if getattr(sys, "frozen", False) else str(ICON_PATH)


# ---------------------------------------------------------------- 图标
def make_icon_image():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 62, 62), radius=13, fill=(28, 115, 245, 255))
    d.polygon([(38, 7), (16, 37), (28, 37), (26, 57), (48, 26), (36, 26)],
              fill=(255, 255, 255, 255))
    return img


# ---------------------------------------------------------------- 托盘应用
def entry_image(entry):
    """取应用条目对应的进程映像名 (如 chrome.exe); 取不到返回 ''。"""
    stop = (entry.get("stop") or "").strip()
    if stop:
        return stop
    p = (entry.get("path") or "").strip()
    if p.lower().endswith((".exe", ".bat", ".cmd")):
        return os.path.basename(p)
    return ""


_RUNNING_CACHE = {"t": 0.0, "names": frozenset()}


def running_images(max_age=2.0):
    """当前所有进程的映像名(小写集合)。一次 tasklist 取全量, 2 秒内复用缓存。"""
    now = time.time()
    if now - _RUNNING_CACHE["t"] < max_age and _RUNNING_CACHE["names"]:
        return _RUNNING_CACHE["names"]
    names = set()
    try:
        rc, out, _ = run_capture(["tasklist", "/FO", "CSV", "/NH"], timeout=25)
        if rc == 0:
            for line in out.splitlines():
                line = line.strip()
                if not line.startswith('"'):
                    continue
                nm = line[1:].split('"', 1)[0].strip().lower()
                if nm:
                    names.add(nm)
    except Exception:
        log_exc()
    _RUNNING_CACHE["t"] = now
    _RUNNING_CACHE["names"] = frozenset(names)
    return _RUNNING_CACHE["names"]


def _colorref(hex_color):
    """'#RRGGBB' -> DWM 需要的 COLORREF 值。"""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r | (g << 8) | (b << 16)


def apply_dark_titlebar(win):
    """把窗口标题栏也变成深色, 去掉顶部白边 (Win10 20H1+ / Win11)。"""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        dwm = ctypes.windll.dwmapi
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                              ctypes.c_void_p, wintypes.DWORD]
        dwm.DwmSetWindowAttribute.restype = ctypes.c_long
        raw = int(win.winfo_id())
        hwnd = user32.GetAncestor(ctypes.c_void_p(raw & 0xFFFFFFFFFFFFFFFF), 2)
        if not hwnd:
            return
        one = ctypes.c_int(1)
        hr = dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(one), 4)
        log("dark titlebar attr20 hr=%s hwnd=%s" % (hr, hwnd))
        if hr != 0:
            hr = dwm.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(one), 4)
            log("dark titlebar attr19 hr=%s" % (hr,))
        for attr, color in ((35, THEME["bg"]), (36, THEME["fg"]),
                            (34, THEME["line"])):
            try:
                c = ctypes.c_int(_colorref(color))
                h2 = dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(c), 4)
                log("dark titlebar color attr%s hr=%s" % (attr, h2))
            except Exception:
                pass
    except Exception:
        log_exc()


class QuickLauncher:
    def __init__(self, icon):
        self.icon = icon
        self.volume = Volume()
        self.config = self.load_config()
        self.mode_pids = {}  # 模式名 -> {exe 映像名: set(本次启动的 pid)}
        self.tk_queue = queue.Queue()  # 托盘线程 -> 编辑窗口线程 的命令队列
        self.tk_thread = None
        self.editor_win = None  # EditorWindow 实例 (由 tk 线程创建)

    # ---- 配置
    def load_config(self):
        if CONFIG_PATH.exists():
            try:
                cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                for k, v in DEFAULT_CONFIG.items():
                    cfg.setdefault(k, v)
                return cfg
            except Exception as e:
                log("config load failed: %r" % (e,))
        self.save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)

    def save_config(self, cfg=None):
        cfg = cfg if cfg is not None else self.config
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

    def spawn(self, fn):
        def run():
            try:
                fn()
            except Exception:
                log_exc()
                self.icon.notify("操作出错，详见 data/log.txt", APP_TITLE)
        threading.Thread(target=run, daemon=True).start()

    def refresh_menu(self):
        try:
            self.icon.menu = self.build_menu()
            self.icon.update_menu()
        except Exception:
            log_exc()

    # ---- 菜单动作
    def launch_app(self, path):
        try:
            os.startfile(path)
        except Exception as e:
            log("launch failed: %r" % (e,))
            self.icon.notify("启动失败: %s\n%s" % (path, e), APP_TITLE)

    def merge_detected(self):
        def run():
            if APPS_DETECTED.exists():
                try:
                    items = json.loads(APPS_DETECTED.read_text(encoding="utf-8"))
                except Exception:
                    items = []
            else:
                items, _ = detect_apps()
            existing = {a.get("path") for a in self.config["apps"]}
            added = [a for a in items if a["path"] not in existing]
            self.config["apps"].extend(added)
            self.save_config()
            self.refresh_menu()
            self.icon.notify("已加入 %d 个应用，当前共 %d 个。\n可通过 编辑配置 调整。" %
                             (len(added), len(self.config["apps"])), APP_TITLE)
        self.spawn(run)

    def edit_config(self):
        try:
            run_detached(["notepad", str(CONFIG_PATH)])
        except Exception as e:
            log("notepad failed: %r" % (e,))

    def reload_config(self):
        self.config = self.load_config()
        self.refresh_menu()
        self.icon.notify("配置已重新加载。", APP_TITLE)

    # ---- 自定义模式 (一键启动/停止一组应用)
    def _stop_name(self, entry):
        stop = (entry.get("stop") or "").strip()
        if stop:
            return stop
        p = (entry.get("path") or "").strip()
        if p.lower().endswith((".exe", ".bat", ".cmd")):
            return os.path.basename(p)
        return ""

    def _proc_pids(self, imagename):
        if not imagename:
            return set()
        try:
            rc, so, _ = run_capture(
                ["tasklist", "/FI", "IMAGENAME eq %s" % imagename, "/FO", "CSV", "/NH"],
                timeout=20)
            pids = set()
            for line in so.splitlines():
                parts = [x.strip().strip('"') for x in line.split('","')]
                if len(parts) >= 2 and parts[0].lower() == imagename.lower():
                    try:
                        pids.add(int(parts[1]))
                    except ValueError:
                        pass
            return pids
        except Exception:
            log_exc()
            return set()

    @staticmethod
    def _kill_pids(pids):
        killed = 0
        for pid in list(pids):
            try:
                rc, so, _ = run_capture(["taskkill", "/PID", str(pid), "/F"],
                                        timeout=15)
                if rc == 0 or "成功" in so:
                    killed += 1
            except Exception:
                log_exc()
        return killed

    def _launch_tracked(self, mode_name, entry, running=None):
        """启动一个应用并记录它新产生的进程 pid。
        已在运行的应用直接跳过。返回 'ok' / 'skip' / 'fail'。"""
        img = entry_image(entry)
        if running is None:
            running = running_images()
        if img and img.lower() in running:
            log("mode launch skipped (already running): %s" % img)
            return "skip"
        stop_name = self._stop_name(entry)
        before = self._proc_pids(stop_name)
        try:
            os.startfile(entry["path"])
        except Exception as e:
            log("mode launch failed: %r" % (e,))
            self.icon.notify("启动失败: %s\n%s" % (entry.get("name"), e), APP_TITLE)
            return "fail"
        if stop_name:
            time.sleep(1.5)
            new = self._proc_pids(stop_name) - before
            key = (img or stop_name).lower()
            store = self.mode_pids.setdefault(mode_name, {})
            store.setdefault(key, set()).update(new)
        return "ok"

    def start_mode(self, mode):
        def run():
            mname = (mode or {}).get("name", "模式")
            msgs = self._apply_mode_settings(mode)
            entries = (mode or {}).get("apps") or []
            running = running_images()
            ok = skip = fail = 0
            for e in entries:
                r = self._launch_tracked(mname, e, running)
                if r == "ok":
                    ok += 1
                elif r == "skip":
                    skip += 1
                else:
                    fail += 1
            gok = gskip = 0
            gapps = self.global_mode_apps()
            if gapps:
                gstore = self.mode_pids.setdefault(GLOBAL_MODE_NAME, {})
                for e in gapps:
                    r = self._launch_tracked(GLOBAL_MODE_NAME, e, running)
                    if r == "ok":
                        gok += 1
                    elif r == "skip":
                        gskip += 1
            parts = ["「%s」已启动 %d/%d 个应用" % (mname, ok, len(entries))]
            if skip:
                parts.append("已在运行, 跳过 %d 个" % skip)
            if fail:
                parts.append("启动失败 %d 个" % fail)
            if gok or gskip:
                parts.append("全局模式 %d 个应用已就绪" % (gok + gskip))
            if msgs:
                parts.append("已应用: " + "、".join(msgs))
            try:
                self.config["current_mode"] = mname
                self.save_config()
                self.refresh_menu()
            except Exception:
                log_exc()
            self.icon.notify("\n".join(parts), APP_TITLE)
        self.spawn(run)

    def stop_mode(self, mode):
        def run():
            entries = (mode or {}).get("apps") or []
            mname = (mode or {}).get("name", "模式")
            store = self.mode_pids.get(mname, {})
            total = 0
            keep_imgs = self._global_keep_images()
            for e in entries:
                key = (entry_image(e) or e.get("name", "?")).lower()
                if key in keep_imgs:
                    continue
                pids = store.get(key, set())
                if pids:
                    total += self._kill_pids(pids)
                    store[key] = set()
            if total:
                self.icon.notify("「%s」已停止 %d 个进程" % (mname, total), APP_TITLE)
            else:
                self.icon.notify("「%s」没有记录到本次启动的进程。\n先用「启动全部」，再用「停止全部」。" % mname,
                                 APP_TITLE)
        self.spawn(run)

    def start_one(self, mode, entry):
        def run():
            r = self._launch_tracked(mode.get("name", "模式"), entry)
            if r == "skip":
                self.icon.notify("「%s」已经在运行, 已跳过" % entry.get("name", "?"), APP_TITLE)
        self.spawn(run)

    def stop_one(self, mode, entry):
        def run():
            mname = mode.get("name", "模式")
            store = self.mode_pids.get(mname, {})
            key = (entry_image(entry) or entry.get("name", "?")).lower()
            if key in self._global_keep_images():
                self.icon.notify("「%s」在全局保留列表里, 不会被停止" % entry.get("name", "?"),
                                 APP_TITLE)
                return
            pids = store.get(key, set())
            if not pids:
                self.icon.notify("「%s」没有记录到本次启动的进程" % entry.get("name", "?"), APP_TITLE)
                return
            k = self._kill_pids(pids)
            store[key] = set()
            self.icon.notify("已停止「%s」%d 个进程" % (entry.get("name", "?"), k), APP_TITLE)
        self.spawn(run)

    # ---- 模式附带设置 (分辨率/音量) 与快速切换
    def _apply_mode_settings(self, mode):
        """应用模式保存的显示/音量设置, 返回已应用的说明列表。"""
        st = (mode or {}).get("settings") or {}
        msgs = []
        vol = st.get("volume")
        if vol is not None:
            try:
                v = current_volume()
                if v:
                    p = max(0, min(100, int(vol)))
                    v.set(p / 100.0)
                    msgs.append("音量 %d%%" % p)
            except Exception:
                log_exc()
        disp = st.get("display")
        if disp:
            try:
                if disp.get("recommended"):
                    r = reset_display(None)
                    msgs.append("显示恢复推荐 (%s)" % CHANGE_ERR.get(r, "结果 %d" % r))
                else:
                    w, h, hz = int(disp["w"]), int(disp["h"]), int(disp["hz"])
                    b = 32
                    for m in enum_modes(None):
                        if m[0] == w and m[1] == h and m[2] == hz:
                            b = m[3]
                            break
                    r = switch_mode(None, w, h, hz, b)
                    msgs.append("显示 %s (%s)" % (fmt_mode((w, h, hz, b)),
                                                 CHANGE_ERR.get(r, "结果 %d" % r)))
            except Exception:
                log_exc()
        return msgs

    @staticmethod
    def _stop_store(store):
        total = 0
        for name, pids in list(store.items()):
            if pids:
                total += QuickLauncher._kill_pids(pids)
                store[name] = set()
        return total

    def stop_all_modes(self):
        def run():
            total = 0
            for store in list(self.mode_pids.values()):
                total += self._stop_store(store)
            try:
                self.config["current_mode"] = ""
                self.save_config()
                self.refresh_menu()
            except Exception:
                log_exc()
            self.icon.notify("已停止本次启动的 %d 个进程" % total, APP_TITLE)
        self.spawn(run)

    def _mode_by_name(self, name):
        for m in (self.config.get("modes") or []):
            if (m.get("name") or "") == name:
                return m
        return None

    def global_mode_apps(self):
        """全局模式的应用列表(所有模式都会自动带上它们)。"""
        gm = self.config.get("global_mode") or {}
        apps = gm.get("apps") if isinstance(gm, dict) else None
        return [a for a in (apps or []) if isinstance(a, dict)]

    def _global_keep_images(self):
        """切换模式时不会被停止的 exe 映像名集合(小写): 全局模式的应用 + 旧的 global_keep。"""
        out = set()
        for e in self.global_mode_apps():
            img = entry_image(e).lower()
            if img:
                out.add(img)
        for it in (self.config.get("global_keep") or []):
            img = ""
            if isinstance(it, str):
                img = it.strip()
            elif isinstance(it, dict):
                img = (it.get("image") or it.get("stop") or "").strip()
                if not img:
                    p = (it.get("path") or "").strip()
                    if p:
                        img = os.path.basename(p)
            if not img:
                continue
            img = img.lower()
            if "." not in img:
                img += ".exe"
            out.add(img)
        return out

    def quick_switch(self, mode):
        """右键托盘的快速切换: 停掉不属于本模式的应用(全局保留 + 本模式要用的除外)
        → 应用显示/音量设置 → 启动本模式(已在运行的跳过)。"""
        def run():
            mname = (mode or {}).get("name", "模式")
            wanted = {}
            for e in ((mode or {}).get("apps") or []):
                img = entry_image(e).lower()
                if img:
                    wanted[img] = True
            keep = dict(wanted)
            for img in self._global_keep_images():
                keep.setdefault(img, True)
            stopped_apps = stopped_pids = kept = 0
            dst_store = self.mode_pids.setdefault(mname, {})
            for other, store in list(self.mode_pids.items()):
                for img, pids in list(store.items()):
                    if not pids:
                        continue
                    if img in keep:
                        # 本模式要用 / 全局保留: 不停止, 归属转给新模式, 之后仍能一键停止
                        if other != mname:
                            dst_store.setdefault(img, set()).update(pids)
                            store[img] = set()
                            kept += 1
                            log("mode switch kept running: %s" % img)
                        continue
                    n = self._kill_pids(pids)
                    if n:
                        stopped_apps += 1
                        stopped_pids += n
                        log("mode switch stopped: %s (%d)" % (img, n))
                    store[img] = set()
            msgs = self._apply_mode_settings(mode)
            entries = (mode or {}).get("apps") or []
            running = running_images()
            ok = skip = fail = 0
            for e in entries:
                r = self._launch_tracked(mname, e, running)
                if r == "ok":
                    ok += 1
                elif r == "skip":
                    skip += 1
                else:
                    fail += 1
            gok = gskip = 0
            gapps = self.global_mode_apps()
            if gapps:
                gstore = self.mode_pids.setdefault(GLOBAL_MODE_NAME, {})
                for e in gapps:
                    r = self._launch_tracked(GLOBAL_MODE_NAME, e, running)
                    if r == "ok":
                        gok += 1
                    elif r == "skip":
                        gskip += 1
            parts = ["「%s」已启动 %d/%d 个应用" % (mname, ok, len(entries))]
            if skip:
                parts.append("已在运行, 跳过 %d 个" % skip)
            if fail:
                parts.append("启动失败 %d 个" % fail)
            if gok or gskip:
                parts.append("全局模式 %d 个应用已就绪" % (gok + gskip))
            if msgs:
                parts.append("已应用: " + "、".join(msgs))
            if kept:
                parts.append("保留运行 %d 个(本模式要用 / 全局保留)" % kept)
            if stopped_pids:
                parts.append("已停掉不属于本模式的 %d 个应用 / %d 个进程"
                             % (stopped_apps, stopped_pids))
            try:
                self.config["current_mode"] = mname
                self.save_config()
                self.refresh_menu()
            except Exception:
                log_exc()
            self.icon.notify("\n".join(parts), APP_TITLE)
        self.spawn(run)

    # ---- 托盘自愈: 图标在则 NIM_MODIFY 刷新, 不在则 NIM_ADD 重建; 返回 'modify'/'add'/None
    def reassert_tray(self):
        try:
            icon = self.icon
            hwnd = getattr(icon, "_hwnd", None)
            if not hwnd:
                return None
            from pystray import _util
            win32 = _util.win32
            icon._assert_icon_handle()
            hicon = getattr(icon, "_icon_handle", None)
            if not hicon:  # LoadImage 失败时兜底: 直接加载我们的 icon.ico
                try:
                    hicon = ctypes.windll.user32.LoadImageW(
                        None, str(ICON_PATH), 1, 0, 0, 0x10 | 0x20)
                    if hicon:
                        icon._icon_handle = hicon
                except Exception:
                    pass
            nid = win32.NOTIFYICONDATAW(
                cbSize=ctypes.sizeof(win32.NOTIFYICONDATAW),
                hWnd=hwnd,
                hID=id(icon),
                uFlags=win32.NIF_MESSAGE | win32.NIF_ICON | win32.NIF_TIP,
                uCallbackMessage=win32.WM_NOTIFY,
                hIcon=hicon or None,
                szTip=(icon.title or APP_TITLE))
            if win32.Shell_NotifyIcon(win32.NIM_MODIFY, nid):
                return "modify"
            if win32.Shell_NotifyIcon(win32.NIM_ADD, nid):
                return "add"
            return None
        except Exception:
            log_exc()
            return None

    def _tray_watchdog(self):
        time.sleep(3)
        first = True
        last = None
        while True:
            try:
                st = self.reassert_tray()
                if first or st != last:
                    log("tray watchdog: %s" % (st if st else "FAILED"))
                    first = False
                    last = st
            except Exception:
                log_exc()
            time.sleep(30)

    def _patch_tray_clicks(self, icon):
        """双击托盘图标打开整体编辑窗口; 单击无动作; 右键弹快速切换菜单。"""
        try:
            from pystray import _util
            win32 = _util.win32
            orig = icon._message_handlers.get(win32.WM_NOTIFY)

            def handler(wparam, lparam):
                if lparam == 0x0203:          # WM_LBUTTONDBLCLK 双击 → 编辑窗口
                    try:
                        self.open_editor()
                    except Exception:
                        log_exc()
                elif lparam == 0x0202:        # WM_LBUTTONUP 单击: 无动作
                    pass
                else:                          # 右键等: 走原逻辑(弹出菜单)
                    orig(wparam, lparam)

            icon._message_handlers[win32.WM_NOTIFY] = handler
            log("tray clicks patched: dblclick=editor, rclick=menu")
        except Exception:
            log_exc()

    def show_net(self):
        def run():
            self.icon.notify(net_status_text(), "网络状态")
        self.spawn(run)

    def proxy_on(self):
        en, _ = get_proxy()
        return en

    def toggle_proxy(self):
        en, srv = get_proxy()
        new = not en
        set_proxy(new, self.config.get("proxy_server", "127.0.0.1:7890"))
        self.refresh_menu()
        self.icon.notify("系统代理已%s" % ("开启" if new else "关闭"), APP_TITLE)

    def do_dns(self, preset):
        servers = preset["servers"]
        name = preset["name"]
        self.spawn(lambda: apply_dns(servers, lambda m: self.icon.notify(m, "DNS 切换")))

    def do_wifi(self, ssid_name):
        self.spawn(lambda: connect_wifi(ssid_name, lambda m: self.icon.notify(m, "WiFi 切换")))

    def enable_all(self):
        def run():
            names = get_all_adapters()
            if not names:
                self.icon.notify("未发现网卡。", APP_TITLE)
                return
            cmds = [" && ".join(
                'netsh interface set interface name="%s" admin=enable' % n
                for n in names)]
            run_smart(cmds[0], "已请求启用全部网卡 (%d 个)" % len(names),
                      lambda m: self.icon.notify(m, APP_TITLE))
        self.spawn(run)

    def disable_active(self):
        def run():
            ads = get_up_adapters()
            if not ads:
                self.icon.notify("当前没有活动网卡。", APP_TITLE)
                return
            name = ads[0]
            set_adapter_state(name, False,
                              lambda m: self.icon.notify(m, APP_TITLE))
        self.spawn(run)

    def toggle_mute(self):
        if not self.volume.available():
            self.icon.notify("未找到音频设备。", APP_TITLE)
            return
        new = not self.volume.muted()
        self.volume.set_mute(new)
        self.refresh_menu()
        self.icon.notify("已%s" % ("静音" if new else "恢复声音"), APP_TITLE)

    def set_vol(self, x):
        if not self.volume.available():
            self.icon.notify("未找到音频设备。", APP_TITLE)
            return
        self.volume.set(x)
        self.refresh_menu()
        self.icon.notify("音量: %d%%" % int(x * 100), APP_TITLE)

    # ---- 编辑窗口里的即时操作 (spawn 线程, 各自取当前线程的 COM 对象)
    def apply_volume(self, pct):
        def run():
            v = current_volume()
            if not v:
                self.icon.notify("未找到音频设备。", APP_TITLE)
                return
            p = max(0, min(100, int(pct)))
            v.set(p / 100.0)
            self.icon.notify("音量: %d%%" % p, APP_TITLE)
        self.spawn(run)

    def apply_mute_toggle(self):
        def run():
            v = current_volume()
            if not v:
                self.icon.notify("未找到音频设备。", APP_TITLE)
                return
            m = not bool(v.muted())
            v.set_mute(m)
            self.icon.notify("已%s" % ("静音" if m else "恢复声音"), APP_TITLE)
        self.spawn(run)

    def apply_display(self, m):
        def run():
            r = switch_mode(None, m[0], m[1], m[2], m[3])
            self.icon.notify("%s: %s" % (fmt_mode(m), CHANGE_ERR.get(r, "结果 %d" % r)),
                             "显示设置")
        self.spawn(run)

    def apply_display_reset(self):
        def run():
            r = reset_display(None)
            self.icon.notify("恢复推荐模式: %s" % CHANGE_ERR.get(r, "结果 %d" % r),
                             "显示设置")
        self.spawn(run)

    def do_switch_mode(self, name, m):
        r = switch_mode(name, m[0], m[1], m[2], m[3])
        self.icon.notify("%s → %s: %s" % ((name or "主显示器"), fmt_mode(m),
                                          CHANGE_ERR.get(r, "未知结果(%d)" % r)),
                         "显示设置")
        time.sleep(0.3)
        self.refresh_menu()

    def do_reset_display(self, name):
        r = reset_display(name)
        self.icon.notify("已恢复推荐模式: %s" % CHANGE_ERR.get(r, "结果 %d" % r),
                         "显示设置")
        time.sleep(0.3)
        self.refresh_menu()

    def redetect(self):
        def run():
            self.icon.notify("正在检测系统配置，请稍候…", APP_TITLE)
            report, summary = do_detection()
            self.icon.notify("检测完成\n" + summary, APP_TITLE)
            self.refresh_menu()
            log("redetect done: %s" % report)
        self.spawn(run)

    def open_report(self):
        try:
            reports = sorted(REPORTS.glob("检测报告_*.txt"),
                             key=lambda p: p.stat().st_mtime)
            if reports:
                os.startfile(reports[-1])
            else:
                self.icon.notify("还没有检测报告，请先重新检测。", APP_TITLE)
        except Exception as e:
            log("open report failed: %r" % (e,))

    def open_folder(self):
        try:
            os.startfile(BASE)
        except Exception:
            pass

    def autostart_state(self):
        return autostart_lnk().exists()

    def toggle_autostart(self):
        p = autostart_lnk()
        try:
            if p.exists():
                p.unlink()
                self.icon.notify("已取消开机自启。", APP_TITLE)
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                exe, args = launch_target()
                create_shortcut(p, exe, args=args,
                                workdir=str(BASE), icon=launch_icon(),
                                description="快捷启动台.exe")
                self.icon.notify("已设置开机自启 (开机自动静默启动)。", APP_TITLE)
        except Exception as e:
            log("autostart failed: %r" % (e,))
            self.icon.notify("开机自启设置失败: %s" % e, APP_TITLE)
        self.refresh_menu()

    def restart_admin(self):
        try:
            exe, args = launch_target()
            r = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", exe, args, str(BASE), 1)
            if r > 32:
                threading.Timer(1.5, self.icon.stop).start()
            else:
                self.icon.notify("已取消提权重启。", APP_TITLE)
        except Exception as e:
            log("restart admin failed: %r" % (e,))

    def quit(self):
        # 先让编辑窗口线程退出, 再停托盘
        try:
            self.tk_queue.put(("quit",))
        except Exception:
            pass
        self.icon.stop()

    # ---- 整体编辑窗口 (Tkinter 独立线程; 最小化/点 ✕ 隐藏到托盘)
    def open_editor(self):
        """从托盘打开整体编辑窗口。"""
        if self.tk_thread is None or not self.tk_thread.is_alive():
            self.tk_thread = threading.Thread(target=self._tk_main, daemon=True)
            self.tk_thread.start()
        try:
            self.tk_queue.put(("show",))
        except Exception:
            pass

    def _tk_main(self):
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()  # 先隐藏, 等收到 show 命令再显示
        self.editor_win = EditorWindow(root, self)

        def poll():
            try:
                while True:
                    cmd = self.tk_queue.get_nowait()
                    if cmd[0] == "show":
                        self.editor_win.show()
                    elif cmd[0] == "quit":
                        try:
                            self.editor_win.destroy()
                        except Exception:
                            pass
                        root.destroy()
                        return
            except queue.Empty:
                pass
            except Exception:
                log_exc()
                return
            try:
                root.after(200, poll)
            except Exception:
                pass
        root.after(200, poll)
        root.mainloop()
        self.editor_win = None

    # ---- 菜单 (右键托盘: 仅快速切换模式)
    def build_menu(self):
        from pystray import Menu, MenuItem
        items = []
        modes = self.config.get("modes") or []
        if modes:
            cur = (self.config.get("current_mode") or "").strip()
            for md in modes:
                name = md.get("name", "模式")
                tag = " ⚙" if (md.get("settings") or {}) else ""
                mark = "   ✅ 当前" if name == cur else ""
                items.append(MenuItem("🚀 " + name + tag + mark,
                                      act(lambda md=md: self.quick_switch(md))))
            items.append(Menu.SEPARATOR)
            items.append(MenuItem("🛑 全部停止 (本次启动的)", act(self.stop_all_modes)))
        else:
            items.append(MenuItem("🚀 快速切换模式 (未配置)", act(self.open_editor)))
        items.append(Menu.SEPARATOR)
        items.append(MenuItem("⚙ 整体编辑 (设置窗口)", act(self.open_editor)))
        items.append(MenuItem("❌ 退出程序", act(self.quit)))
        return Menu(*items)

    def display_menu_items(self, name, label):
        from pystray import Menu, MenuItem
        cur = get_mode(name)
        modes = enum_modes(name)
        modes = sorted(modes, key=lambda m: (-(m[0] * m[1]), -m[2]))
        mitems = []
        for m in modes:
            checked = cur and (cur[0], cur[1], cur[2]) == (m[0], m[1], m[2])
            mitems.append(MenuItem(fmt_mode(m),
                                   act(lambda m=m: self.do_switch_mode(name, m)),
                                   checked=lambda item, mm=m, cc=cur:
                                   cc and (cc[0], cc[1], cc[2]) == (mm[0], mm[1], mm[2])))
        mitems.append(Menu.SEPARATOR)
        mitems.append(MenuItem("恢复推荐模式", act(lambda: self.do_reset_display(name))))
        return mitems

    # ---- 启动
    def on_ready(self, icon):
        # pystray: 自定义 setup 回调里必须手动设 visible=True，否则托盘图标永不显示
        try:
            icon.visible = True
        except Exception:
            log_exc()
        self._patch_tray_clicks(icon)   # 双击托盘 → 编辑窗口; 右键 → 快速切换菜单
        icon.notify("已启动，正在自动检测本机配置…", APP_TITLE)
        self.spawn(self._startup_detect)
        threading.Thread(target=self._tray_watchdog, daemon=True).start()

    def _warm_editor(self):
        """后台提前建好编辑窗口(隐藏), 双击托盘时秒开。"""
        try:
            if self.tk_thread is None or not self.tk_thread.is_alive():
                self.tk_thread = threading.Thread(target=self._tk_main, daemon=True)
                self.tk_thread.start()
                log("editor warmed (hidden)")
        except Exception:
            log_exc()

    def _startup_detect(self):
        try:
            report, summary = do_detection()
            # 首次启动: 把检测到的应用(已排除系统内置)自动填入配置
            if not self.config.get("apps_seeded"):
                try:
                    if APPS_DETECTED.exists():
                        items = json.loads(APPS_DETECTED.read_text(encoding="utf-8"))
                        if isinstance(items, list):
                            self.config["apps"] = items
                            self.config["apps_seeded"] = True
                            self.save_config()
                            log("apps seeded: %d" % len(items))
                            summary += "\n已自动获取 %d 个应用 (排除系统内置)" % len(items)
                except Exception:
                    log_exc()
            self.icon.notify("配置检测完成\n" + summary, APP_TITLE)
            self.refresh_menu()
            log("startup detect done: %s" % report)
            self._warm_editor()
        except Exception:
            log_exc()
            self.icon.notify("配置检测失败，详见 data/log.txt", APP_TITLE)


# ---------------------------------------------------------------- 整体编辑窗口
class EditorWindow:
    """整体编辑窗口 (六页): 应用列表 / 自定义模式 / 网络设置 / 音量 / 显示设置 / 系统。
    自定义模式可给每个模式保存 音量+分辨率刷新率 设置, 启动该模式时自动应用。
    最小化或点 ✕ 时隐藏到托盘 (程序不退出)。所有方法只在 tk 线程内调用。"""

    def __init__(self, root, app):
        self.root = root
        self.app = app
        self.apps_work = []
        self.modes_work = []
        self.dns_work = []
        self.detected = []
        self.detected_names = []
        self.cur_mode_idx_settings = None   # 当前正在编辑设置的模式索引
        self.global_mode_work = {"apps": []}   # 全局模式的应用(所有模式自动包含)
        self._icon_cache = {}              # 应用路径 -> PhotoImage (带缓存)
        self._default_icon = None
        self._done_q = queue.Queue()       # 后台线程 -> tk 线程的结果队列
        self._dead = False
        self._cfg_mtime = None
        self._net_loading = False
        root.title("快捷启动台 · 整体编辑")
        root.geometry("980x820+120+40")
        root.protocol("WM_DELETE_WINDOW", self.hide)
        root.bind("<Unmap>", self._on_unmap)
        try:
            root.iconbitmap(str(ICON_PATH))
        except Exception:
            pass
        self._build()
        try:
            self.root.configure(bg=THEME["bg"])
        except Exception:
            pass
        try:
            self.root.update_idletasks()
            apply_dark_titlebar(self.root)
        except Exception:
            pass
        try:
            self.root.bind("<Map>", self._on_map_theme, add="+")
        except Exception:
            pass
        try:
            self.root.after(80, self._pump_done)
        except Exception:
            pass

    # ---------- 通用
    def destroy(self):
        """退出前释放控件/变量引用, 让它们在 tk 线程内被回收。"""
        self._dead = True
        for attr in ("tv_apps", "tv_mapps", "tv_pool", "tv_dns", "lb_modes", "cb_pick",
                     "status", "var_name", "var_path", "var_pick", "var_mname",
                     "var_mpath", "var_mstop", "var_proxy", "var_dnsname", "var_dnssrv",
                     "var_modevol", "cb_modedisp", "var_modedisp", "lbl_msettings",
                     "var_vol", "scale_vol", "lbl_vol",
                     "var_disp", "cb_disp", "lbl_disp", "disp_map", "mode_disp_map",
                     "lbl_auto",
                     "nb_net", "tv_wifi", "lbl_wifi_cur", "var_wpass", "ent_wpass",
                     "btn_wshow", "lbl_wauth", "var_wauto",
                     "tv_lan", "lbl_lan_cur", "var_lanip", "var_lanmask",
                     "var_langw", "var_landns1", "var_landns2",
                     "var_pool_search", "ent_pool_search", "lbl_pool_count",
                     "global_mode_work", "tv_scan", "scan_body", "btn_saved_toggle", "saved_body",
                     "lbl_scan", "txt_sys", "tv_res",
                     "btn_wifi_radio", "var_autorefresh", "lbl_net_rate"):
            try:
                setattr(self, attr, None)
            except Exception:
                pass
        self._icon_cache = {}
        self._default_icon = None
        self._pool_rows = []

    def set_status(self, text):
        try:
            self.status.config(text=text)
        except Exception:
            pass

    def hide(self):
        try:
            self.root.withdraw()
        except Exception:
            pass

    def _on_map_theme(self, ev=None):
        # 窗口每次显示(Tk 会重设标题栏样式)都重新套用深色
        try:
            if ev is not None and getattr(ev, "widget", None) is not self.root:
                return
            self.root.after(10, lambda: apply_dark_titlebar(self.root))
        except Exception:
            pass

    def _on_unmap(self, ev):
        # 最小化 -> 隐藏到托盘
        try:
            if self.root.state() == "iconic":
                self.root.after(10, self.hide)
        except Exception:
            pass

    def _topmost_off(self):
        try:
            self.root.attributes("-topmost", False)
        except Exception:
            pass

    def show(self):
        try:
            _t_shown = time.time()
            try:
                cur = CONFIG_PATH.stat().st_mtime
            except Exception:
                cur = None
            if cur != self._cfg_mtime:
                self.refresh()
            else:
                try:
                    self.lbl_chip.configure(
                        text="◆ 管理员权限" if is_admin() else "◇ 标准权限")
                except Exception:
                    pass
                self._net_load_async()
            self.root.deiconify()
            try:
                apply_dark_titlebar(self.root)
            except Exception:
                pass
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(300, self._topmost_off)
            self.root.focus_force()
            self.root.update_idletasks()
            log("editor shown in %.0f ms" % ((time.time() - _t_shown) * 1000.0,))
        except Exception:
            log_exc()

    # ---------- 界面搭建
    def _setup_theme(self, root):
        """深色科技风主题: clam 主题 + 自定义配色。"""
        from tkinter import ttk
        C = THEME
        try:
            root.configure(bg=C["bg"])
            root.option_add("*TCombobox*Listbox.background", C["panel2"])
            root.option_add("*TCombobox*Listbox.foreground", C["fg"])
            root.option_add("*TCombobox*Listbox.selectBackground", C["accent2"])
            root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
            root.option_add("*TCombobox*Listbox.font", ("Microsoft YaHei UI", 9))
        except Exception:
            pass
        st = ttk.Style(root)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        base_font = ("Microsoft YaHei UI", 9)
        try:
            st.configure(".", background=C["panel"], foreground=C["fg"],
                         fieldbackground=C["panel2"], bordercolor=C["line"],
                         lightcolor=C["panel"], darkcolor=C["panel"],
                         troughcolor=C["bg"], focuscolor=C["accent"],
                         font=base_font, borderwidth=0)
            st.configure("TFrame", background=C["panel"])
            st.configure("Header.TFrame", background=C["bg"])
            st.configure("TLabel", background=C["panel"], foreground=C["fg"])
            st.configure("Dim.TLabel", background=C["panel"], foreground=C["dim"])
            st.configure("Accent.TLabel", background=C["panel"], foreground=C["accent"],
                         font=("Microsoft YaHei UI", 9, "bold"))
            st.configure("H1.TLabel", background=C["bg"], foreground=C["accent"],
                         font=("Microsoft YaHei UI", 15, "bold"))
            st.configure("Sub.TLabel", background=C["bg"], foreground=C["dim"],
                         font=("Microsoft YaHei UI", 9))
            st.configure("Status.TLabel", background=C["panel"], foreground=C["dim"])
            st.configure("TSeparator", background=C["line"], borderwidth=0)
            # 按钮
            st.configure("TButton", background=C["panel2"], foreground=C["fg"],
                         bordercolor=C["line"], lightcolor=C["panel2"],
                         darkcolor=C["panel"], padding=(10, 6), relief="flat")
            st.map("TButton",
                   background=[("disabled", C["panel2"]), ("pressed", "#20304f"),
                               ("active", "#1a2740")],
                   foreground=[("disabled", C["dim"])])
            st.configure("Accent.TButton", background=C["accent"], foreground="#04141f",
                         bordercolor=C["accent"], lightcolor=C["accent"],
                         darkcolor=C["accent"], padding=(12, 6), relief="flat",
                         font=("Microsoft YaHei UI", 9, "bold"))
            st.map("Accent.TButton",
                   background=[("pressed", "#0fb8d4"), ("active", "#4ae3f7")])
            st.configure("Ghost.TButton", background=C["panel"], foreground=C["dim"],
                         bordercolor=C["line"], lightcolor=C["panel"],
                         darkcolor=C["panel"], padding=(10, 6), relief="flat")
            st.map("Ghost.TButton",
                   background=[("pressed", "#1a2740"), ("active", "#16213a")],
                   foreground=[("active", C["fg"])])
            # 输入
            for nm in ("TEntry", "TCombobox"):
                st.configure(nm, fieldbackground=C["panel2"], foreground=C["fg"],
                             background=C["panel2"], bordercolor=C["line"],
                             lightcolor=C["line"], darkcolor=C["line"],
                             insertcolor=C["accent"], padding=4)
                st.map(nm, fieldbackground=[("readonly", C["panel2"])],
                       foreground=[("readonly", C["fg"])])
            st.configure("TCombobox", arrowsize=14, arrowcolor=C["accent"])
            # 标签页
            st.configure("TNotebook", background=C["bg"], bordercolor=C["line"],
                         borderwidth=0, tabmargins=(4, 6, 4, 0))
            st.configure("TNotebook.Tab", background=C["panel"],
                         foreground=C["dim"], padding=(16, 8),
                         bordercolor=C["line"], lightcolor=C["panel"],
                         darkcolor=C["panel"], font=("Microsoft YaHei UI", 9))
            st.map("TNotebook.Tab",
                   background=[("selected", C["panel2"]), ("active", C["panel3"])],
                   foreground=[("selected", C["accent"]), ("active", C["fg"])])
            # 表格
            st.configure("Treeview", background=C["panel2"],
                         fieldbackground=C["panel2"], foreground=C["fg"],
                         bordercolor=C["line"], lightcolor=C["panel2"],
                         darkcolor=C["panel2"], rowheight=36, borderwidth=0)
            st.map("Treeview",
                   background=[("selected", C["sel"])],
                   foreground=[("selected", "#ffffff")])
            st.configure("Treeview.Heading", background=C["panel"],
                         foreground=C["accent"], relief="flat",
                         bordercolor=C["line"], lightcolor=C["panel"],
                         darkcolor=C["panel"], padding=(8, 6),
                         font=("Microsoft YaHei UI", 9, "bold"))
            st.map("Treeview.Heading", background=[("active", C["panel3"])])
            # 滚动条
            for nm in ("TScrollbar", "Vertical.TScrollbar", "Horizontal.TScrollbar"):
                st.configure(nm, background=C["panel"], troughcolor=C["bg"],
                             bordercolor=C["bg"], arrowcolor=C["dim"],
                             lightcolor=C["panel"], darkcolor=C["panel"],
                             gripcount=0, width=12)
                st.map(nm, background=[("active", C["panel3"])])
            # 勾选框 / 滑块 / 分组框
            st.configure("TCheckbutton", background=C["panel"], foreground=C["fg"],
                         indicatorcolor=C["panel2"], indicatorbackground=C["panel2"])
            st.map("TCheckbutton", background=[("active", C["panel"])],
                   indicatorbackground=[("selected", C["accent"])])
            st.configure("TScale", background=C["panel"], troughcolor=C["panel2"],
                         bordercolor=C["line"], lightcolor=C["accent"],
                         darkcolor=C["panel"], sliderrelief="flat")
            st.configure("TLabelframe", background=C["panel"], bordercolor=C["line"],
                         lightcolor=C["line"], darkcolor=C["line"],
                         relief="solid", borderwidth=1)
            st.configure("TLabelframe.Label", background=C["panel"],
                         foreground=C["accent"],
                         font=("Microsoft YaHei UI", 9, "bold"))
        except Exception as e:
            log("theme setup error: %r" % (e,))

    def _build(self):
        import tkinter as tk
        from tkinter import ttk
        self._setup_theme(self.root)
        # 行高 / 留白: Treeview 默认行高太矮, 32px 图标会挤在一起
        style = ttk.Style()
        try:
            style.configure("Treeview", rowheight=36)
            style.configure("Treeview.Heading", padding=(8, 6))
            style.configure("TNotebook.Tab", padding=(16, 8))
            style.configure("TSeparator", rowheight=1)
        except Exception:
            pass
        # 顶部标题栏
        hdr = ttk.Frame(self.root, style="Header.TFrame")
        hdr.pack(fill="x", padx=14, pady=(10, 0))
        ttk.Label(hdr, text="⚡ 快捷启动台", style="H1.TLabel").pack(side="left")
        ttk.Label(hdr, text="QuickLauncher %s" % APP_VER,
                  style="Sub.TLabel").pack(side="left", padx=(12, 0), pady=(8, 0))
        self.lbl_chip = ttk.Label(hdr, text="", style="Accent.TLabel")
        self.lbl_chip.pack(side="right", padx=(0, 10), pady=(6, 0))
        ttk.Separator(self.root, orient="horizontal").pack(
            fill="x", padx=14, pady=(8, 0))
        nb = self.nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=10, pady=(6, 4))
        nb.add(self._tab_apps(), text="  🚀 应用列表  ")
        nb.add(self._tab_modes(), text="  🚀 自定义模式  ")
        nb.add(self._tab_net(), text="  🌐 网络设置  ")
        nb.add(self._tab_sys(), text="  ⚙ 系统  ")
        bar = ttk.Frame(self.root)
        bar.pack(fill="x", padx=12, pady=(4, 10))
        self.status = ttk.Label(
            bar, style="Status.TLabel",
            text="提示: 最小化或点 ✕ 会隐藏到托盘; 双击托盘图标可重新打开。右键托盘 = 快速切换模式。")
        self.status.pack(side="left", padx=6)
        ttk.Button(bar, text="⏬ 隐藏到托盘", style="Ghost.TButton",
                   command=self.hide).pack(side="right")
        ttk.Button(bar, text="💾 保存并生效", style="Accent.TButton",
                   command=self.save).pack(side="right", padx=8)
        # 斑马纹
        for tv in ("tv_apps", "tv_mapps", "tv_pool", "tv_dns", "tv_wifi", "tv_lan", "tv_res"):
            w = getattr(self, tv, None)
            try:
                if w is not None:
                    w.tag_configure("zebra", background=THEME["row2"])
            except Exception:
                pass

    def _tab_apps(self):
        import tkinter as tk
        from tkinter import ttk
        f = ttk.Frame(self.root, padding=10)
        g = ttk.Frame(f)
        g.pack(fill="both", expand=True)
        cols = ("name", "path")
        self.tv_apps = ttk.Treeview(g, columns=cols, show="tree headings")
        self.tv_apps.heading("#0", text="")
        self.tv_apps.heading("name", text="名称")
        self.tv_apps.heading("path", text="路径")
        self.tv_apps.column("#0", width=56, minwidth=56, stretch=False)
        self.tv_apps.column("name", width=220, anchor="w")
        self.tv_apps.column("path", width=610, anchor="w")
        self.tv_apps.bind("<Double-1>", self._app_fill)
        sb = ttk.Scrollbar(g, orient="vertical", command=self.tv_apps.yview)
        self.tv_apps.configure(yscrollcommand=sb.set)
        self.tv_apps.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        g.rowconfigure(0, weight=1)
        g.columnconfigure(0, weight=1)

        row = ttk.Frame(f)
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text="名称").pack(side="left")
        self.var_name = tk.StringVar()
        ttk.Entry(row, textvariable=self.var_name, width=22).pack(side="left", padx=(4, 10))
        ttk.Label(row, text="路径").pack(side="left")
        self.var_path = tk.StringVar()
        ttk.Entry(row, textvariable=self.var_path, width=52).pack(side="left", padx=(4, 6))
        ttk.Button(row, text="浏览…", command=self._browse).pack(side="left", padx=(0, 10))

        row2 = ttk.Frame(f)
        row2.pack(fill="x", pady=6)
        ttk.Button(row2, text="➕ 添加 / 更新 应用", command=self._app_add).pack(side="left")
        ttk.Button(row2, text="🗑 删除选中", command=self._app_del).pack(side="left", padx=8)
        ttk.Label(row2, text="从检测库选择:").pack(side="left", padx=(16, 4))
        self.var_pick = tk.StringVar()
        self.cb_pick = ttk.Combobox(row2, textvariable=self.var_pick, width=30, state="readonly")
        self.cb_pick.pack(side="left")
        self.cb_pick.bind("<<ComboboxSelected>>", self._pick_detected)
        ttk.Button(row2, text="⬆ 填入上方", command=self._pick_detected).pack(side="left", padx=6)
        ttk.Button(row2, text="🔄 一键填充全部应用", command=self._apps_fill_all).pack(side="left", padx=(12, 0))
        ttk.Label(f, foreground=THEME["dim"],
                  text="首次启动会默认自动获取全部应用(已排除系统内置)。双击列表行可回填到上方修改。"
                       "保存后才写入 config.json。"
                  ).pack(anchor="w", pady=(4, 0))
        return f

    def _tab_modes(self):
        import tkinter as tk
        from tkinter import ttk
        f = ttk.Frame(self.root, padding=10)
        left = ttk.Frame(f)
        left.pack(side="left", fill="y", padx=(0, 12))
        ttk.Label(left, text="模式").pack(anchor="w")
        self.lb_modes = tk.Listbox(left, width=20, exportselection=False,
                                   font=("Microsoft YaHei UI", 10), activestyle="none",
                                   selectborderwidth=0, highlightthickness=1,
                                   bg=THEME["panel2"], fg=THEME["fg"],
                                   selectbackground=THEME["sel"], selectforeground="#ffffff",
                                   highlightbackground=THEME["line"],
                                   highlightcolor=THEME["accent"],
                                   borderwidth=0, relief="flat")
        self.lb_modes.pack(fill="y", expand=True, pady=4)
        self.lb_modes.bind("<<ListboxSelect>>", self._mode_selected)
        ttk.Button(left, text="➕ 添加模式", command=self._mode_add).pack(fill="x", pady=2)
        ttk.Button(left, text="✏ 重命名", command=self._mode_rename).pack(fill="x", pady=2)
        ttk.Button(left, text="🗑 删除模式", command=self._mode_del).pack(fill="x", pady=2)

        right = ttk.Frame(f)
        right.pack(side="left", fill="both", expand=True)
        self.lbl_msettings = ttk.Label(right, text="", foreground=THEME["accent"])
        self.lbl_msettings.pack(fill="x", pady=(0, 2))
        g = ttk.Frame(right)
        g.pack(fill="both", expand=True)
        cols = ("name", "path", "stop")
        self.tv_mapps = ttk.Treeview(g, columns=cols, show="tree headings", height=5)
        self.tv_mapps.heading("#0", text="")
        self.tv_mapps.heading("name", text="应用名称")
        self.tv_mapps.heading("path", text="路径")
        self.tv_mapps.heading("stop", text="停止进程名 (可空)")
        self.tv_mapps.column("#0", width=56, minwidth=56, stretch=False)
        self.tv_mapps.column("name", width=140, anchor="w")
        self.tv_mapps.column("path", width=370, anchor="w")
        self.tv_mapps.column("stop", width=126, anchor="w")
        self.tv_mapps.bind("<Double-1>", self._mapp_fill)
        sb = ttk.Scrollbar(g, orient="vertical", command=self.tv_mapps.yview)
        self.tv_mapps.configure(yscrollcommand=sb.set)
        self.tv_mapps.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        g.rowconfigure(0, weight=1)
        g.columnconfigure(0, weight=1)

        row = ttk.Frame(right)
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="名称").pack(side="left")
        self.var_mname = tk.StringVar()
        ttk.Entry(row, textvariable=self.var_mname, width=16).pack(side="left", padx=(4, 8))
        ttk.Label(row, text="路径").pack(side="left")
        self.var_mpath = tk.StringVar()
        ttk.Entry(row, textvariable=self.var_mpath, width=38).pack(side="left", padx=(4, 6))
        ttk.Button(row, text="浏览…", command=self._browse_mode).pack(side="left", padx=(0, 8))
        ttk.Label(row, text="停止进程").pack(side="left")
        self.var_mstop = tk.StringVar()
        ttk.Entry(row, textvariable=self.var_mstop, width=14).pack(side="left", padx=(4, 0))

        row2 = ttk.Frame(right)
        row2.pack(fill="x", pady=4)
        ttk.Button(row2, text="➕ 添加 / 更新 应用", command=self._mapp_add).pack(side="left")
        ttk.Button(row2, text="🗑 删除选中", command=self._mapp_del).pack(side="left", padx=8)
        ttk.Label(row2, foreground=THEME["dim"],
                  text="停止进程名留空 = 自动用 exe 文件名; 双击行可回填修改。"
                  ).pack(side="left", padx=10)

        self.row2_settings = row2
        self.row3_settings = row3 = ttk.Frame(right)
        row3.pack(fill="x", pady=(0, 2))
        ttk.Label(row3, text="⚙ 启动本模式时自动应用设置:").pack(side="left")
        ttk.Label(row3, text="音量%").pack(side="left", padx=(10, 2))
        self.var_modevol = tk.StringVar()
        self.ent_modevol = ttk.Entry(row3, textvariable=self.var_modevol, width=6)
        self.ent_modevol.pack(side="left")
        self.var_modedisp = tk.StringVar()
        self.cb_modedisp = ttk.Combobox(row3, textvariable=self.var_modedisp,
                                        width=26, state="readonly")
        self.cb_modedisp.pack(side="left", padx=(10, 4))
        self.btn_msave = ttk.Button(row3, text="💾 保存到模式",
                                    command=self._mode_settings_save)
        self.btn_msave.pack(side="left")
        self.lbl_mset_hint = ttk.Label(right, foreground=THEME["dim"],
                                       text="先在左侧选中一个模式再填写; 音量留空 = 不修改音量。")
        self.lbl_mset_hint.pack(anchor="w", pady=(2, 0))

        ttk.Label(right, foreground=THEME["dim"],
                  text="🌍 左侧第一行是「全局模式」: 放进去的应用会被所有模式自动包含, "
                       "启动/切换模式时一起启动, 且不会被切换停止。"
                  ).pack(anchor="w", pady=(2, 0))

        pool = ttk.Frame(right)
        pool.pack(fill="x", pady=(8, 0))
        pool_head = ttk.Frame(pool)
        pool_head.pack(fill="x")
        ttk.Label(pool_head, text="📋 应用库 (单击图标 = 直接添加到当前模式):"
                  ).pack(side="left")
        ttk.Label(pool_head, text="🔎 搜索").pack(side="left", padx=(14, 3))
        self.var_pool_search = tk.StringVar()
        self.ent_pool_search = ttk.Entry(pool_head, textvariable=self.var_pool_search,
                                         width=18)
        self.ent_pool_search.pack(side="left")
        ttk.Label(pool_head, text="名称或路径, 输入即过滤"
                  ).pack(side="left", padx=(6, 0))
        self.lbl_pool_count = ttk.Label(pool_head, text="", style="Dim.TLabel")
        self.lbl_pool_count.pack(side="right", padx=(0, 6))
        self.var_pool_search.trace_add("write", self._on_pool_search)
        pg = ttk.Frame(pool)
        pg.pack(fill="x", pady=4)
        self.tv_pool = ttk.Treeview(pg, columns=("pname", "ppath"),
                                    show="tree headings", height=6)
        self.tv_pool.heading("#0", text="")
        self.tv_pool.heading("pname", text="应用")
        self.tv_pool.heading("ppath", text="路径")
        self.tv_pool.column("#0", width=56, minwidth=56, stretch=False)
        self.tv_pool.column("pname", width=150, anchor="w")
        self.tv_pool.column("ppath", width=500, anchor="w")
        self.tv_pool.bind("<<TreeviewSelect>>", self._pool_pick)
        psb = ttk.Scrollbar(pg, orient="vertical", command=self.tv_pool.yview)
        self.tv_pool.configure(yscrollcommand=psb.set)
        self.tv_pool.grid(row=0, column=0, sticky="nsew")
        psb.grid(row=0, column=1, sticky="ns")
        pg.rowconfigure(0, weight=1)
        pg.columnconfigure(0, weight=1)
        return f

    # ---------------- 全局保留 (旧接口, 保留供配置兼容)
    def _parse_global_keep(self):
        raw = (self.var_global_keep.get() or "")
        for sep in ("，", "；", ";", "、", " "):
            raw = raw.replace(sep, ",")
        out, seen = [], set()
        for piece in raw.split(","):
            img = piece.strip().lower()
            if not img:
                continue
            if "." not in img:
                img += ".exe"
            if img in seen:
                continue
            seen.add(img)
            out.append(img)
        return out

    def _global_keep_text(self):
        items = self.app.config.get("global_keep") or []
        names = []
        for it in items:
            if isinstance(it, str):
                names.append(it)
            elif isinstance(it, dict):
                names.append((it.get("image") or it.get("stop")
                              or os.path.basename((it.get("path") or "").strip()) or ""))
        return ", ".join([n for n in names if n])

    def _keep_from_pool(self):
        rows = getattr(self, "_pool_rows", None) or self.apps_work
        sel = self.tv_pool.selection()
        if not sel:
            self.set_status("先在下方应用库里选中一条")
            return
        try:
            entry = rows[self.tv_pool.index(sel[0])]
        except Exception:
            self.set_status("读取应用库失败")
            return
        img = entry_image(entry)
        if not img:
            self.set_status("这条应用拿不到 exe 名, 请手动填写")
            return
        cur = [x.strip() for x in (self.var_global_keep.get() or "").split(",") if x.strip()]
        if img.lower() not in [c.lower() for c in cur]:
            cur.append(img)
            self.var_global_keep.set(", ".join(cur))
        self.set_status("已加入全局保留: %s (记得点【保存并生效】)" % img)

    def _async(self, fn, done):
        """后台线程执行 fn(), 完成后由 tk 线程调用 done(结果)。
        跨线程不能直接用 root.after (会报 main thread is not in main loop),
        所以结果先进队列, 由 _pump_done 定时取出。"""
        def worker():
            try:
                res = fn()
            except Exception as e:
                res = (False, "出现异常: %r" % (e,))
            try:
                self._done_q.put((done, res))
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    def _pump_done(self):
        """tk 线程里定时取出后台结果并执行回调。"""
        if getattr(self, "_dead", False):
            return
        try:
            while True:
                cb, res = self._done_q.get_nowait()
                try:
                    cb(res)
                except Exception:
                    log_exc()
        except queue.Empty:
            pass
        except Exception:
            log_exc()
        try:
            self.root.after(80, self._pump_done)
        except Exception:
            pass

    def _tab_net(self):
        import tkinter as tk
        from tkinter import ttk
        f = ttk.Frame(self.root, padding=10)
        self.nb_net = ttk.Notebook(f)
        self.nb_net.pack(fill="both", expand=True)
        self.nb_net.add(self._net_tab_wifi(self.nb_net), text="  📶 WiFi (无线)  ")
        self.nb_net.add(self._net_tab_lan(self.nb_net), text="  🔌 以太网 / 网卡  ")
        self.nb_net.add(self._net_tab_proxy(self.nb_net), text="  🌐 代理 / DNS 预设  ")
        return f

    # ---------------- 网络: WiFi 子页
    def _net_tab_wifi(self, parent):
        import tkinter as tk
        from tkinter import ttk
        f = ttk.Frame(parent, padding=8)
        rhead = ttk.Frame(f)
        rhead.pack(fill="x", pady=(0, 8))
        self.btn_wifi_radio = ttk.Button(rhead, text="WiFi: 检测中…",
                                         command=self._wifi_radio_toggle)
        self.btn_wifi_radio.pack(side="left")
        ttk.Label(rhead, foreground=THEME["dim"],
                  text="一键开启 / 关闭 WiFi 无线开关").pack(side="left", padx=8)

        # ---- 主区: 附近 WiFi (扫描)
        main = ttk.Frame(f)
        main.pack(fill="both", expand=True)
        ttk.Label(main, text="📡 附近 WiFi (双击连接; 已保存的网络不在此列)").pack(anchor="w")
        self.lbl_scan = ttk.Label(main, text="", foreground=THEME["dim"])
        self.lbl_scan.pack(anchor="w")
        sg = ttk.Frame(main)
        sg.pack(fill="both", expand=True, pady=4)
        self.tv_scan = ttk.Treeview(sg, columns=("ssid", "signal", "auth", "band"),
                                    show="headings", height=6)
        self.tv_scan.heading("ssid", text="SSID")
        self.tv_scan.heading("signal", text="信号")
        self.tv_scan.heading("auth", text="加密")
        self.tv_scan.heading("band", text="频段/通道")
        self.tv_scan.column("ssid", width=260, anchor="w")
        self.tv_scan.column("signal", width=70, anchor="w")
        self.tv_scan.column("auth", width=140, anchor="w")
        self.tv_scan.column("band", width=120, anchor="w")
        self.tv_scan.bind("<Double-1>", lambda e: self._wifi_scan_connect())
        ssb = ttk.Scrollbar(sg, orient="vertical", command=self.tv_scan.yview)
        self.tv_scan.configure(yscrollcommand=ssb.set)
        self.tv_scan.grid(row=0, column=0, sticky="nsew")
        ssb.grid(row=0, column=1, sticky="ns")
        sg.rowconfigure(0, weight=1)
        sg.columnconfigure(0, weight=1)
        srow = ttk.Frame(main)
        srow.pack(fill="x", pady=(4, 0))
        ttk.Button(srow, text="🌀 重新扫描", command=self._wifi_scan_load).pack(side="left")
        ttk.Button(srow, text="📡 连接所选",
                   command=self._wifi_scan_connect).pack(side="left", padx=6)
        ttk.Label(srow, foreground=THEME["dim"],
                  text="未保存的网络双击=连接(会询问密码); 扫描需无线网卡可用。"
                  ).pack(side="left", padx=10)

        # ---- 已保存 WiFi 折叠区
        self.btn_saved_toggle = ttk.Button(
            f, text="💾 已保存的 WiFi 配置  ▶", command=self._wifi_saved_toggle)
        self.btn_saved_toggle.pack(fill="x", pady=(10, 0))
        self.saved_body = ttk.Frame(f)
        top = ttk.Frame(self.saved_body)
        top.pack(fill="both", expand=True)
        left = ttk.Frame(top)
        left.pack(side="left", fill="both", expand=True)
        self.lbl_wifi_head = ttk.Label(left, text="", foreground=THEME["accent"])
        self.lbl_wifi_head.pack(anchor="w")
        g = ttk.Frame(left)
        g.pack(fill="both", expand=True, pady=4)
        self.tv_wifi = ttk.Treeview(g, columns=("ssid", "state"),
                                    show="headings", height=7)
        self.tv_wifi.heading("ssid", text="WiFi 名称 (SSID)")
        self.tv_wifi.heading("state", text="状态")
        self.tv_wifi.column("ssid", width=280, anchor="w")
        self.tv_wifi.column("state", width=120, anchor="w")
        self.tv_wifi.bind("<<TreeviewSelect>>", self._wifi_sel)
        self.tv_wifi.bind("<Double-1>", lambda e: self._wifi_connect())
        sb = ttk.Scrollbar(g, orient="vertical", command=self.tv_wifi.yview)
        self.tv_wifi.configure(yscrollcommand=sb.set)
        self.tv_wifi.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        g.rowconfigure(0, weight=1)
        g.columnconfigure(0, weight=1)
        row = ttk.Frame(left)
        row.pack(fill="x", pady=(4, 0))
        ttk.Button(row, text="🌀 刷新", command=self._wifi_load).pack(side="left")
        ttk.Button(row, text="📡 连接", command=self._wifi_connect).pack(side="left", padx=6)
        ttk.Button(row, text="✂ 断开", command=self._wifi_disconnect).pack(side="left")
        ttk.Button(row, text="🗑 删除配置", command=self._wifi_del).pack(side="left", padx=6)
        ttk.Button(row, text="⚙ 系统 WiFi 面板",
                   command=self._wifi_open_settings).pack(side="left")

        det = ttk.LabelFrame(top, text="WiFi 配置 (可直接编辑, 保存进系统)")
        det.pack(side="left", fill="y", padx=(10, 0), ipadx=10, ipady=8)
        self.lbl_wifi_cur = ttk.Label(det, text="(左侧选中一个 WiFi 配置)",
                                      foreground=THEME["accent"])
        self.lbl_wifi_cur.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        ttk.Label(det, text="密码").grid(row=1, column=0, sticky="e", pady=4)
        self.var_wpass = tk.StringVar()
        self.ent_wpass = ttk.Entry(det, textvariable=self.var_wpass,
                                   width=26, show="•")
        self.ent_wpass.grid(row=1, column=1, sticky="w", pady=4)
        self.btn_wshow = ttk.Button(det, text="👁 显示", width=8,
                                    command=self._wifi_toggle_pass)
        self.btn_wshow.grid(row=1, column=2, padx=(6, 0))
        ttk.Label(det, text="安全类型").grid(row=2, column=0, sticky="e", pady=4)
        self.lbl_wauth = ttk.Label(det, text="-")
        self.lbl_wauth.grid(row=2, column=1, columnspan=2, sticky="w", pady=4)
        self.var_wauto = tk.BooleanVar(value=True)
        ttk.Checkbutton(det, text="自动连接此 WiFi",
                        variable=self.var_wauto).grid(row=3, column=1, columnspan=2,
                                                      sticky="w", pady=4)
        brow = ttk.Frame(det)
        brow.grid(row=4, column=0, columnspan=3, sticky="w", pady=(12, 4))
        ttk.Button(brow, text="💾 保存修改", command=self._wifi_save).pack(side="left")
        ttk.Button(brow, text="📄 原始配置",
                   command=self._wifi_raw).pack(side="left", padx=8)
        ttk.Label(det, foreground=THEME["dim"], justify="left",
                  text="• 密码留空 = 不修改密码; 保存后重新连接该 WiFi 才生效"
                  ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))
        return f

    # ---------------- 网络: 以太网 / 网卡子页
    def _net_tab_lan(self, parent):
        import tkinter as tk
        from tkinter import ttk
        f = ttk.Frame(parent, padding=8)
        g = ttk.Frame(f)
        g.pack(fill="both", expand=True)
        cols = ("name", "state", "dhcp", "ip")
        self.tv_lan = ttk.Treeview(g, columns=cols, show="headings", height=7)
        self.tv_lan.heading("name", text="网卡名称")
        self.tv_lan.heading("state", text="状态")
        self.tv_lan.heading("dhcp", text="配置方式")
        self.tv_lan.heading("ip", text="IPv4 地址")
        self.tv_lan.column("name", width=310, anchor="w")
        self.tv_lan.column("state", width=100, anchor="w")
        self.tv_lan.column("dhcp", width=110, anchor="w")
        self.tv_lan.column("ip", width=190, anchor="w")
        self.tv_lan.bind("<<TreeviewSelect>>", self._lan_sel)
        sb = ttk.Scrollbar(g, orient="vertical", command=self.tv_lan.yview)
        self.tv_lan.configure(yscrollcommand=sb.set)
        self.tv_lan.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        g.rowconfigure(0, weight=1)
        g.columnconfigure(0, weight=1)

        det = ttk.LabelFrame(f, text="IPv4 配置 (选中网卡后可直接编辑, 点按钮应用)")
        det.pack(fill="x", pady=(8, 0), ipadx=10, ipady=8)
        self.lbl_lan_cur = ttk.Label(det, text="(上方选中一个网卡)",
                                     foreground=THEME["accent"])
        self.lbl_lan_cur.grid(row=0, column=0, columnspan=8, sticky="w", pady=(0, 8))
        ttk.Label(det, text="IP 地址").grid(row=1, column=0, sticky="e", pady=3, padx=(0, 4))
        self.var_lanip = tk.StringVar()
        ttk.Entry(det, textvariable=self.var_lanip, width=17).grid(
            row=1, column=1, sticky="w", padx=(0, 14))
        ttk.Label(det, text="子网掩码").grid(row=1, column=2, sticky="e", pady=3, padx=(0, 4))
        self.var_lanmask = tk.StringVar()
        ttk.Entry(det, textvariable=self.var_lanmask, width=17).grid(
            row=1, column=3, sticky="w", padx=(0, 14))
        ttk.Label(det, text="默认网关").grid(row=1, column=4, sticky="e", pady=3, padx=(0, 4))
        self.var_langw = tk.StringVar()
        ttk.Entry(det, textvariable=self.var_langw, width=17).grid(
            row=1, column=5, sticky="w")
        ttk.Label(det, text="主 DNS").grid(row=2, column=0, sticky="e", pady=3, padx=(0, 4))
        self.var_landns1 = tk.StringVar()
        ttk.Entry(det, textvariable=self.var_landns1, width=17).grid(
            row=2, column=1, sticky="w", padx=(0, 14))
        ttk.Label(det, text="备用 DNS").grid(row=2, column=2, sticky="e", pady=3, padx=(0, 4))
        self.var_landns2 = tk.StringVar()
        ttk.Entry(det, textvariable=self.var_landns2, width=17).grid(
            row=2, column=3, sticky="w", padx=(0, 14))
        b = ttk.Frame(det)
        b.grid(row=3, column=0, columnspan=8, sticky="w", pady=(12, 4))
        ttk.Button(b, text="💾 应用静态配置", command=self._lan_apply).pack(side="left")
        ttk.Button(b, text="↩ 改回 DHCP 自动获取",
                   command=self._lan_dhcp).pack(side="left", padx=8)
        ttk.Button(b, text="🌀 刷新", command=self._lan_load).pack(side="left")
        ttk.Button(b, text="🔌 启用网卡", command=self._lan_enable).pack(side="left", padx=8)
        ttk.Button(b, text="⛔ 禁用网卡", command=self._lan_disable).pack(side="left")
        ttk.Button(b, text="⚙ 网络连接面板",
                   command=self._lan_open_settings).pack(side="left", padx=8)
        ttk.Label(det, foreground=THEME["dim"],
                  text="改 IP / DNS / 启停网卡需要管理员权限; 点按钮后若弹 UAC 请点“是”。改完立即生效。"
                  ).grid(row=4, column=0, columnspan=8, sticky="w")
        return f

    # ---------------- 网络: 代理 / DNS 预设子页
    def _net_tab_proxy(self, parent):
        import tkinter as tk
        from tkinter import ttk
        f = ttk.Frame(parent, padding=8)
        r1 = ttk.Frame(f)
        r1.pack(fill="x", pady=(0, 8))
        ttk.Label(r1, text="系统代理地址 (代理开关使用):").pack(side="left")
        self.var_proxy = tk.StringVar()
        ttk.Entry(r1, textvariable=self.var_proxy, width=32).pack(side="left", padx=6)
        ttk.Label(r1, text="例: 127.0.0.1:7890", foreground=THEME["dim"]).pack(side="left")

        ttk.Label(f, text="DNS 方案预设 (托盘菜单【🌐 DNS 一键切换】使用)").pack(anchor="w")
        g = ttk.Frame(f)
        g.pack(fill="both", expand=True, pady=4)
        cols = ("name", "servers")
        self.tv_dns = ttk.Treeview(g, columns=cols, show="headings")
        self.tv_dns.heading("name", text="方案名称")
        self.tv_dns.heading("servers", text="DNS 服务器 (逗号分隔; 填 dhcp = 自动获取)")
        self.tv_dns.column("name", width=220, anchor="w")
        self.tv_dns.column("servers", width=520, anchor="w")
        self.tv_dns.bind("<Double-1>", self._dns_fill)
        sb = ttk.Scrollbar(g, orient="vertical", command=self.tv_dns.yview)
        self.tv_dns.configure(yscrollcommand=sb.set)
        self.tv_dns.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        g.rowconfigure(0, weight=1)
        g.columnconfigure(0, weight=1)

        row = ttk.Frame(f)
        row.pack(fill="x", pady=6)
        ttk.Label(row, text="名称").pack(side="left")
        self.var_dnsname = tk.StringVar()
        ttk.Entry(row, textvariable=self.var_dnsname, width=22).pack(side="left", padx=(4, 12))
        ttk.Label(row, text="服务器").pack(side="left")
        self.var_dnssrv = tk.StringVar()
        ttk.Entry(row, textvariable=self.var_dnssrv, width=40).pack(side="left", padx=(4, 8))
        ttk.Button(row, text="➕ 添加 / 更新", command=self._dns_add).pack(side="left")
        ttk.Button(row, text="🗑 删除选中", command=self._dns_del).pack(side="left", padx=8)

        ttk.Label(f, text="━━ 快捷操作 ━━").pack(anchor="w", pady=(10, 2))
        row3 = ttk.Frame(f)
        row3.pack(fill="x", pady=4)
        ttk.Button(row3, text="📶 网络状态", command=self._net_status).pack(side="left")
        ttk.Button(row3, text="🪜 系统代理 开关", command=self._net_proxy).pack(side="left", padx=8)
        ttk.Button(row3, text="🌐 应用选中 DNS 方案", command=self._net_dns).pack(side="left")
        ttk.Button(row3, text="🔌 启用全部网卡", command=self._net_enable).pack(side="left", padx=8)
        ttk.Button(row3, text="⛔ 禁用当前活动网卡 (断网!)", command=self._net_disable).pack(side="left")
        ttk.Label(f, foreground=THEME["dim"],
                  text="DNS / 网卡操作需要管理员权限, 会自动弹 UAC 确认。"
                  ).pack(anchor="w", pady=(6, 0))
        return f

    # ---------------- 网络: WiFi 处理
    def _wifi_radio_refresh(self):
        try:
            st = wifi_radio_on()
            if st is True:
                self.btn_wifi_radio.configure(text="WiFi: ✅ 已开启 (点击关闭)")
            elif st is False:
                self.btn_wifi_radio.configure(text="WiFi: ⛔ 已关闭 (点击开启)")
            else:
                self.btn_wifi_radio.configure(text="WiFi: 状态未知 (点击切换)")
            return st
        except Exception:
            return None

    def _wifi_radio_toggle(self):
        cur = wifi_radio_on()
        target = False if (cur is True) else True
        self.btn_wifi_radio.configure(state="disabled", text="WiFi: 正在切换…")
        self.set_status("正在%s WiFi…" % ("关闭" if not target else "开启"))

        def done(res):
            try:
                self.btn_wifi_radio.configure(state="normal")
                if res:
                    self.set_status("WiFi 已%s" % ("关闭" if not target else "开启"))
                else:
                    self.set_status("WiFi 切换失败 (可能需要管理员权限)")
                self._wifi_radio_refresh()
                self._net_load_async()
            except Exception:
                log_exc()
        self._async(lambda: wifi_set_radio(target), done)

    def _wifi_load(self, ev=None):
        """刷新 WiFi 列表 (后台加载, 不卡界面)。"""
        self._net_load_async()

    def _wifi_fill(self, rows, connected, ssid):
        try:
            self.lbl_wifi_head.configure(
                text=("当前: ● " + ssid) if (connected and ssid) else "当前: 未连接 WiFi")
        except Exception:
            pass
        try:
            self.tv_wifi.delete(*self.tv_wifi.get_children())
            for i, r in enumerate(rows):
                self.tv_wifi.insert("", "end",
                                    values=(r["name"],
                                            "● 已连接" if r["connected"] else "已保存"),
                                    tags=("zebra",) if i % 2 else ())
        except Exception:
            pass

    def _net_load_async(self):
        """一次性后台加载 WiFi + 网卡数据, 完成后刷新两张表。"""
        if getattr(self, "_net_loading", False):
            return
        self._net_loading = True

        def work():
            try:
                rows_w = wifi_profiles_status()
            except Exception as e:
                log("wifi load failed: %r" % (e,))
                rows_w = []
            try:
                connected, ssid, _ifname = wlan_ifaces_state()
            except Exception:
                connected, ssid = False, ""
            try:
                rows_l = adapter_list()
            except Exception as e:
                log("adapter list failed: %r" % (e,))
                rows_l = []
            return rows_w, connected, ssid, rows_l

        def done(res):
            self._net_loading = False
            try:
                rows_w, connected, ssid, rows_l = res
            except Exception:
                return
            self._wifi_fill(rows_w, connected, ssid)
            self._lan_fill(rows_l)
            st = None
            try:
                st = self._wifi_radio_refresh()
            except Exception:
                pass
            try:
                if st is True and not self.tv_scan.get_children():
                    self._wifi_scan_load()
            except Exception:
                pass

        try:
            self._async(work, done)
        except Exception:
            self._net_loading = False

    def _wifi_sel(self, ev=None):
        sel = self.tv_wifi.selection()
        if not sel:
            return
        name = self.tv_wifi.item(sel[0], "values")[0]
        self.lbl_wifi_cur.configure(text="WiFi: " + name + "  (读取中…)")
        self.var_wpass.set("")
        self.lbl_wauth.configure(text="读取中…")

        def done(res):
            d = res if isinstance(res, dict) else {}
            self.lbl_wifi_cur.configure(text="WiFi: " + name)
            self.var_wpass.set(d.get("password", ""))
            self.lbl_wauth.configure(text=(d.get("auth") or "-"))
            self.var_wauto.set(bool(d.get("auto", True)))

        self._async(lambda: wifi_profile_edit(name), done)

    def _wifi_current(self):
        sel = self.tv_wifi.selection()
        if not sel:
            self.set_status("先在左边选中一个 WiFi 配置")
            return ""
        return self.tv_wifi.item(sel[0], "values")[0]

    def _wifi_toggle_pass(self):
        if self.ent_wpass.cget("show"):
            self.ent_wpass.configure(show="")
            self.btn_wshow.configure(text="🙈 隐藏")
        else:
            self.ent_wpass.configure(show="•")
            self.btn_wshow.configure(text="👁 显示")

    def _net_done(self, res):
        ok, msg = res if isinstance(res, tuple) else (True, str(res))
        self.set_status(("✅ " if ok else "❌ ") + str(msg))
        try:
            self._net_load_async()
        except Exception:
            pass

    def _wifi_save(self):
        name = self._wifi_current()
        if not name:
            return
        pwd = self.var_wpass.get().strip()
        auto = bool(self.var_wauto.get())
        self.set_status("正在写入 WiFi 配置…")
        self._async(lambda: wifi_save_profile(name, pwd, auto), self._net_done)

    def _wifi_connect(self):
        name = self._wifi_current()
        if not name:
            return
        self.set_status("正在连接 WiFi「%s」…" % name)
        self._async(lambda: wifi_connect_name(name), self._net_done)

    def _wifi_disconnect(self):
        self._async(wifi_disconnect_now, self._net_done)

    def _wifi_del(self):
        name = self._wifi_current()
        if not name:
            return
        from tkinter import messagebox
        if not messagebox.askyesno("删除 WiFi 配置",
                                   "确定删除 WiFi 配置「%s」?\n删除后需要重新输入密码连接。"
                                   % name, parent=self.root):
            return
        self.set_status("正在删除…")
        self._async(lambda: wifi_delete_profile(name), self._net_done)

    def _wifi_open_settings(self):
        open_windows_setting("ms-settings:network-wifi")

    def _wifi_raw(self):
        name = self._wifi_current()
        if not name:
            return
        import tkinter as tk
        rc, txt = _netsh(["wlan", "show", "profile", "name=" + name, "key=clear"])
        win = tk.Toplevel(self.root)
        win.title("原始配置: " + name)
        win.geometry("660x520")
        t = tk.Text(win, wrap="word", font=("Consolas", 9),
                    bg=THEME["panel2"], fg=THEME["fg"],
                    insertbackground=THEME["accent"], relief="flat",
                    padx=10, pady=8)
        t.pack(fill="both", expand=True)
        t.insert("1.0", txt)

    # ---------------- 网络: 附近 WiFi 扫描
    def _wifi_saved_toggle(self):
        if self.saved_body.winfo_ismapped():
            self.saved_body.pack_forget()
            self.btn_saved_toggle.configure(text="💾 已保存的 WiFi 配置  ▶")
        else:
            self.saved_body.pack(fill="both", expand=True)
            self.btn_saved_toggle.configure(text="💾 已保存的 WiFi 配置  ▼")

    def _wifi_scan_load(self):
        self.lbl_scan.configure(text="正在扫描附近 WiFi…")

        def done(res):
            try:
                rows = res if isinstance(res, list) else []
            except Exception:
                rows = []
            self._wifi_scan_fill(rows)

        self._async(wifi_scan_networks, done)

    def _wifi_scan_fill(self, rows):
        try:
            self.tv_scan.delete(*self.tv_scan.get_children())
            for i, r in enumerate(rows):
                self.tv_scan.insert(
                    "", "end",
                    values=(r["ssid"],
                            ("%d%%" % r["signal"]) if r.get("signal") is not None else "-",
                            r.get("auth") or "-", r.get("band") or "-"),
                    tags=("zebra",) if i % 2 else ())
        except Exception:
            pass
        self.lbl_scan.configure(text="扫描到 %d 个未保存的网络 (按信号降序)" % len(rows))

    def _wifi_scan_connect(self):
        sel = self.tv_scan.selection()
        if not sel:
            self.set_status("先在附近 WiFi 列表里选中一个网络")
            return
        vals = self.tv_scan.item(sel[0], "values")
        ssid, auth = vals[0], vals[2]
        need_pwd = bool(auth) and ("开放" not in auth and "Open" not in auth)
        pwd = ""
        if need_pwd:
            import tkinter.simpledialog as sd
            pwd = sd.askstring("连接 WiFi", "输入「%s」的密码:" % ssid,
                               parent=self.root)
            if pwd is None:
                return
            pwd = pwd.strip()
            if not pwd:
                self.set_status("密码不能为空, 已取消")
                return
        self.set_status("正在连接「%s」…" % ssid)

        def work():
            return wifi_connect_new(ssid, pwd, auth)

        def done(res):
            ok, msg = res if isinstance(res, tuple) else (True, str(res))
            self.set_status(("✅ " if ok else "❌ ") + str(msg))
            if ok:
                self._wifi_load()

        self._async(work, done)

    # ---------------- 网络: 网卡处理

    def _lan_load(self, ev=None):
        """刷新网卡列表 (后台加载, 不卡界面)。"""
        self._net_load_async()

    def _lan_fill(self, rows):
        try:
            self.tv_lan.delete(*self.tv_lan.get_children())
            for i, r in enumerate(rows):
                self.tv_lan.insert("", "end",
                                   values=(r["name"],
                                           "● 已连接" if r["connected"] else "○ 已断开",
                                           "DHCP" if r["dhcp"] else "静态",
                                           r["ip"] or "-"),
                                   tags=("zebra",) if i % 2 else ())
        except Exception:
            pass

    def _lan_sel(self, ev=None):
        sel = self.tv_lan.selection()
        if not sel:
            return
        name = self.tv_lan.item(sel[0], "values")[0]
        self.lbl_lan_cur.configure(text="网卡: " + name + "  (读取中…)")

        def done(res):
            d = res if isinstance(res, dict) else {}
            if d.get("dhcp") is None:
                suffix = ""
            elif d.get("dhcp"):
                suffix = "   (DHCP 自动获取)"
            else:
                suffix = "   (静态)"
            self.lbl_lan_cur.configure(text="网卡: " + name + suffix)
            self.var_lanip.set(d.get("ip", ""))
            self.var_lanmask.set(d.get("mask", ""))
            self.var_langw.set(d.get("gw", ""))
            dns = d.get("dns") or []
            self.var_landns1.set(dns[0] if dns else "")
            self.var_landns2.set(dns[1] if len(dns) > 1 else "")

        self._async(lambda: adapter_config(name), done)

    def _lan_current(self):
        sel = self.tv_lan.selection()
        if not sel:
            self.set_status("先在上方选中一个网卡")
            return ""
        return self.tv_lan.item(sel[0], "values")[0]

    def _lan_apply(self):
        name = self._lan_current()
        if not name:
            return
        ip = self.var_lanip.get().strip()
        mask = self.var_lanmask.get().strip()
        gw = self.var_langw.get().strip()
        d1 = self.var_landns1.get().strip()
        d2 = self.var_landns2.get().strip()
        if not ip or not mask:
            self.set_status("IP 地址和子网掩码不能为空")
            return
        self.set_status("正在应用静态配置…")
        self._async(lambda: adapter_set_static(name, ip, mask, gw, d1, d2),
                    self._net_done)

    def _lan_dhcp(self):
        name = self._lan_current()
        if not name:
            return
        self.set_status("正在改回 DHCP…")
        self._async(lambda: adapter_set_dhcp(name), self._net_done)

    def _lan_enable(self):
        name = self._lan_current()
        if not name:
            return
        self._async(lambda: adapter_enable(name, True), self._net_done)

    def _lan_disable(self):
        name = self._lan_current()
        if not name:
            return
        from tkinter import messagebox
        if not messagebox.askyesno("禁用网卡",
                                   "禁用网卡「%s」可能会断开网络, 确定?" % name,
                                   parent=self.root):
            return
        self._async(lambda: adapter_enable(name, False), self._net_done)

    def _lan_open_settings(self):
        open_windows_setting("control:ncpa.cpl")


    def _tab_vol(self):
        import tkinter as tk
        from tkinter import ttk
        f = ttk.Frame(self.root, padding=16)
        self.var_vol = tk.IntVar(value=50)
        self.scale_vol = ttk.Scale(f, from_=0, to=100, length=430,
                                   variable=self.var_vol, command=self._vol_slide)
        self.scale_vol.pack(pady=(8, 4))
        row = ttk.Frame(f)
        row.pack()
        ttk.Label(row, text="音量:").pack(side="left")
        self.lbl_vol = ttk.Label(row, text="50%", width=5)
        self.lbl_vol.pack(side="left")
        ttk.Button(row, text="🔊 应用音量", command=self._vol_apply).pack(side="left", padx=12)
        ttk.Button(row, text="🔇 静音 / 恢复", command=self._vol_mute).pack(side="left")
        row2 = ttk.Frame(f)
        row2.pack(pady=8)
        for p in (20, 40, 60, 80, 100):
            ttk.Button(row2, text="%d%%" % p,
                       command=lambda p=p: self._vol_set(p)).pack(side="left", padx=4)
        ttk.Label(f, foreground=THEME["dim"],
                  text="自定义模式启动时也会自动应用其保存的音量设置。"
                  ).pack(pady=(6, 0))
        return f

    def _tab_display(self):
        import tkinter as tk
        from tkinter import ttk
        f = ttk.Frame(self.root, padding=16)
        self.lbl_disp = ttk.Label(f, text="当前模式: (读取中…)")
        self.lbl_disp.pack(anchor="w")
        row = ttk.Frame(f)
        row.pack(pady=10)
        ttk.Label(row, text="选择模式:").pack(side="left")
        self.var_disp = tk.StringVar()
        self.cb_disp = ttk.Combobox(row, textvariable=self.var_disp, width=40, state="readonly")
        self.cb_disp.pack(side="left", padx=6)
        ttk.Button(row, text="🖥 应用所选模式", command=self._disp_apply).pack(side="left", padx=(10, 6))
        ttk.Button(row, text="↩ 恢复推荐模式", command=self._disp_reset).pack(side="left")
        ttk.Label(f, foreground=THEME["dim"],
                  text="自定义模式启动时也会自动应用其保存的显示设置。"
                  ).pack(pady=(6, 0))
        return f

    def _tab_sys(self):
        import tkinter as tk
        from tkinter import ttk
        f = ttk.Frame(self.root, padding=16)
        head = ttk.Frame(f)
        head.pack(fill="x")
        self.lbl_auto = ttk.Label(head, text="")
        self.lbl_auto.pack(side="left")
        self.var_autorefresh = tk.BooleanVar(value=False)
        ttk.Checkbutton(head, text="⏱ 动态刷新 (每 5 秒)",
                        variable=self.var_autorefresh).pack(side="left", padx=12)
        ttk.Button(head, text="🌀 刷新系统信息",
                   command=self._sys_info_load).pack(side="right")

        g = ttk.Frame(f)
        g.pack(fill="x", pady=8)
        self.txt_sys = tk.Text(g, wrap="word", font=("Consolas", 9),
                               bg=THEME["panel2"], fg=THEME["fg"],
                               insertbackground=THEME["accent"], relief="flat",
                               padx=12, pady=10, height=13)
        sb = ttk.Scrollbar(g, orient="vertical", command=self.txt_sys.yview)
        self.txt_sys.configure(yscrollcommand=sb.set)
        self.txt_sys.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        g.rowconfigure(0, weight=1)
        g.columnconfigure(0, weight=1)

        res = ttk.LabelFrame(f, text="📊 当前模式应用资源 (全局模式 + 当前模式)", padding=6)
        res.pack(fill="both", expand=True, pady=(0, 8))
        rhead = ttk.Frame(res)
        rhead.pack(fill="x")
        self.lbl_net_rate = ttk.Label(rhead, foreground=THEME["accent"],
                                      text="🌐 网络速率: 计算中…")
        self.lbl_net_rate.pack(side="left")
        ttk.Label(rhead, foreground=THEME["dim"],
                  text="内存 / CPU / 磁盘 / GPU 逐应用采样 · 网络为整机速率").pack(side="left", padx=10)
        ttk.Button(rhead, text="🔄 刷新资源",
                   command=self._res_load).pack(side="right")
        self.tv_res = ttk.Treeview(
            res, columns=("app", "state", "cnt", "cpu", "mem", "disk", "gpu"),
            show="headings", height=7)
        self.tv_res.heading("app", text="应用")
        self.tv_res.heading("state", text="状态")
        self.tv_res.heading("cnt", text="进程")
        self.tv_res.heading("cpu", text="CPU")
        self.tv_res.heading("mem", text="内存")
        self.tv_res.heading("disk", text="磁盘")
        self.tv_res.heading("gpu", text="GPU")
        self.tv_res.column("app", width=200, anchor="center")
        self.tv_res.column("state", width=72, anchor="center")
        self.tv_res.column("cnt", width=52, anchor="center")
        self.tv_res.column("cpu", width=70, anchor="center")
        self.tv_res.column("mem", width=90, anchor="center")
        self.tv_res.column("disk", width=90, anchor="center")
        self.tv_res.column("gpu", width=60, anchor="center")
        rsb = ttk.Scrollbar(res, orient="vertical", command=self.tv_res.yview)
        self.tv_res.configure(yscrollcommand=rsb.set)
        self.tv_res.pack(side="left", fill="both", expand=True, pady=(4, 0))
        rsb.pack(side="right", fill="y", pady=(4, 0))

        row = ttk.Frame(f)
        row.pack(fill="x", pady=(0, 8))
        ttk.Button(row, text="🚀 开机自启 开 / 关", command=self._sys_autostart).pack(side="left")
        ttk.Button(row, text="🩺 重新检测本机配置", command=self._sys_redetect).pack(side="left", padx=8)
        ttk.Button(row, text="📄 打开检测报告", command=self._sys_report).pack(side="left")
        ttk.Button(row, text="📁 打开程序文件夹", command=self._sys_folder).pack(side="left", padx=8)
        ttk.Button(row, text="🔓 以管理员身份重启", command=self._sys_admin).pack(side="left")
        ttk.Button(row, text="❌ 退出程序", command=self._sys_quit).pack(side="left", padx=8)
        ttk.Label(f, foreground=THEME["dim"],
                  text="双击托盘图标可重新打开本窗口; 右键托盘 = 快速切换模式。"
                  ).pack(anchor="w")
        self._sys_info_load()
        self.root.after(5000, self._autorefresh_tick)
        return f

    # ---------- 音量/显示/系统页动作
    def _vol_slide(self, v):
        try:
            self.lbl_vol.config(text="%d%%" % int(float(v)))
        except Exception:
            pass

    def _vol_apply(self):
        self.app.apply_volume(self.var_vol.get())
        self.set_status("已应用音量 %d%%" % self.var_vol.get())

    def _vol_set(self, p):
        self.var_vol.set(p)
        self.lbl_vol.config(text="%d%%" % p)
        self.app.apply_volume(p)
        self.set_status("已应用音量 %d%%" % p)

    def _vol_mute(self):
        self.app.apply_mute_toggle()
        self.set_status("已切换静音")

    def _disp_apply(self):
        label = self.var_disp.get()
        m = self.disp_map.get(label)
        if m:
            self.app.apply_display(m)
            self.set_status("已切换到 %s" % label)
        else:
            self.set_status("请先选择显示模式")

    def _disp_reset(self):
        self.app.apply_display_reset()
        self.set_status("已恢复推荐模式")

    def _sys_autostart(self):
        self.app.toggle_autostart()
        self._sys_refresh_state()
        self.set_status("开机自启状态已切换")

    def _sys_refresh_state(self):
        try:
            self.lbl_auto.config(
                text="开机自启: %s" % ("✔ 已开启" if self.app.autostart_state() else "✘ 未开启"))
        except Exception:
            pass

    def _sys_redetect(self):
        self.app.redetect()
        self.set_status("已开始重新检测, 完成后会弹出通知")

    def _sys_report(self):
        self.app.open_report()

    def _sys_folder(self):
        self.app.open_folder()

    def _sys_admin(self):
        self.app.restart_admin()

    def _sys_quit(self):
        self.app.quit()

    def _sys_info_load(self):
        self.txt_sys.configure(state="normal")
        self.txt_sys.delete("1.0", "end")
        self.txt_sys.insert("1.0", "正在读取系统信息…")
        self.txt_sys.configure(state="disabled")

        def done(res):
            try:
                text = format_system_info(res)
            except Exception:
                text = "读取系统信息失败"
            try:
                self.txt_sys.configure(state="normal")
                self.txt_sys.delete("1.0", "end")
                self.txt_sys.insert("1.0", text)
                self.txt_sys.configure(state="disabled")
            except Exception:
                pass

        self._async(collect_system_detailed, done)
        try:
            self._res_load()
        except Exception:
            log_exc()

    def _autorefresh_tick(self):
        if getattr(self, "_dead", False):
            return
        try:
            if (getattr(self, "var_autorefresh", None)
                    and self.var_autorefresh.get()
                    and self.nb.index(self.nb.select()) == 3):
                self._res_load()
        except Exception:
            pass
        try:
            self.root.after(5000, self._autorefresh_tick)
        except Exception:
            pass

    def _res_load(self):
        """统计当前模式(全局模式 + 当前模式)各应用的资源占用。"""
        if getattr(self, "_res_loading", False):
            return
        self._res_loading = True
        cfg = self.app.config
        entries = []
        seen = set()
        for e in ((cfg.get("global_mode") or {}).get("apps") or []):
            if not isinstance(e, dict):
                continue
            img = _img_key(entry_image(e))
            if img and img not in seen:
                seen.add(img)
                entries.append(e)
        cur = (cfg.get("current_mode") or "").strip()
        for md in (cfg.get("modes") or []):
            if not isinstance(md, dict) or md.get("name") != cur:
                continue
            for e in (md.get("apps") or []):
                if not isinstance(e, dict):
                    continue
                img = _img_key(entry_image(e))
                if img and img not in seen:
                    seen.add(img)
                    entries.append(e)
            break

        def done(res):
            self._res_loading = False
            try:
                if isinstance(res, dict):
                    self._res_fill(res)
                else:
                    self._res_fill({"rows": res if isinstance(res, list) else [],
                                    "down_bps": 0.0, "up_bps": 0.0})
            except Exception:
                log_exc()
        try:
            self._async(lambda: mode_resource_usage(entries), done)
        except Exception:
            self._res_loading = False
            log_exc()

    def _res_fill(self, data):
        rows = (data or {}).get("rows") or []
        self.tv_res.delete(*self.tv_res.get_children())
        if hasattr(self, "lbl_net_rate"):
            try:
                self.lbl_net_rate.config(text="🌐 网络速率: ↓ %s · ↑ %s" % (
                    _fmt_rate(data.get("down_bps", 0.0)),
                    _fmt_rate(data.get("up_bps", 0.0))))
            except Exception:
                pass
        for i, r in enumerate(rows):
            gpu = r.get("gpu_pct", 0.0) or 0.0
            self.tv_res.insert(
                "", "end", iid=str(i),
                values=(r.get("name") or r.get("image", ""),
                        "运行中" if r.get("running") else "未运行",
                        r.get("count", 0),
                        "%.1f%%" % max(0.0, r.get("cpu_pct", 0.0)),
                        "%.1f MB" % r.get("mem_mb", 0.0),
                        "%.1f MB/s" % (r.get("disk_bps", 0.0) / 1048576.0),
                        "%.0f%%" % max(0.0, gpu)),
                tags=("zebra",) if i % 2 else ())

    def _net_status(self):
        self.app.show_net()

    def _net_proxy(self):
        self.app.toggle_proxy()
        self.set_status("系统代理: %s" % ("已开启" if self.app.proxy_on() else "已关闭"))

    def _net_dns(self):
        sel = self.tv_dns.selection()
        if not sel:
            self.set_status("请先在 DNS 列表里选中一个方案")
            return
        vals = self.tv_dns.item(sel[0], "values")
        name = vals[0]
        for d in self.dns_work:
            if d["name"] == name:
                self.app.do_dns(d)
                self.set_status("已应用 DNS 方案「%s」" % name)
                return

    def _net_wifi(self):
        ssid = self.cb_wifi.get().strip()
        if ssid:
            self.app.do_wifi(ssid)
            self.set_status("正在连接 WiFi「%s」…" % ssid)

    def _net_enable(self):
        self.app.enable_all()

    def _net_disable(self):
        self.app.disable_active()

    def _apps_fill_all(self):
        self.app.merge_detected()
        self.set_status("已把检测到的应用(排除系统内置)合并进列表, 点【💾 保存并生效】")
        try:
            self.refresh()
        except Exception:
            pass

    # ---------- 应用图标
    def _get_icon(self, path):
        """应用路径 -> PhotoImage(带缓存); 提取失败用字母兜底图标。"""
        key = path or ""
        img = self._icon_cache.get(key)
        if img is not None:
            return img
        photo = None
        if path:
            try:
                from PIL import ImageTk
                pil = load_cached_icon(path)
                if pil is None:
                    pil = extract_icon(path)
                    if pil is not None:
                        save_cached_icon(path, pil)
                if pil:
                    photo = ImageTk.PhotoImage(pil)
            except Exception:
                photo = None
        if photo is None:
            if self._default_icon is None:
                try:
                    from PIL import ImageTk
                    self._default_icon = ImageTk.PhotoImage(make_letter_icon("A"))
                except Exception:
                    self._default_icon = None
            photo = self._default_icon
        self._icon_cache[key] = photo
        return photo

    # ---------- 数据加载
    def refresh(self):
        try:
            self._cfg_mtime = CONFIG_PATH.stat().st_mtime
        except Exception:
            self._cfg_mtime = None
        cfg = self.app.config
        self.apps_work = [dict(a) for a in (cfg.get("apps") or []) if isinstance(a, dict)]
        self.modes_work = [
            {"name": str(m.get("name", "")),
             "apps": [dict(e) for e in (m.get("apps") or []) if isinstance(e, dict)],
             "settings": dict(m.get("settings") or {})}
            for m in (cfg.get("modes") or []) if isinstance(m, dict)]
        self.dns_work = [
            {"name": str(d.get("name", "")), "servers": list(d.get("servers") or [])}
            for d in (cfg.get("dns_presets") or []) if isinstance(d, dict)]
        self.var_proxy.set(str(cfg.get("proxy_server", "")))
        gm = cfg.get("global_mode") or {}
        self.global_mode_work = {
            "apps": [dict(e) for e in ((gm.get("apps") if isinstance(gm, dict) else None) or [])
                     if isinstance(e, dict)]}
        self.detected = []
        if APPS_DETECTED.exists():
            try:
                self.detected = json.loads(APPS_DETECTED.read_text(encoding="utf-8"))
            except Exception:
                self.detected = []
        self.detected_names = [
            str(d.get("name", "?")) + "  |  " + str(d.get("path", "")) for d in self.detected]
        try:
            self.cb_pick["values"] = self.detected_names
            self.var_pick.set("")
        except Exception:
            pass

        # 显示模式表: label -> (w,h,hz,bpp)
        self.disp_map = {}
        self.mode_disp_map = {}
        try:
            modes = enum_modes(None)
            modes = sorted(modes, key=lambda m: (-(m[0] * m[1]), -m[2]))
            for m in modes:
                label = fmt_mode(m)
                self.disp_map[label] = m
                self.mode_disp_map[label] = {"w": m[0], "h": m[1], "hz": m[2]}
            self.cb_modedisp["values"] = ["不改显示", "恢复推荐模式"] + list(self.mode_disp_map)
            self.var_modedisp.set("不改显示")
        except Exception:
            log_exc()
        # 网络信息(WiFi/网卡)用后台线程加载, 不阻塞窗口打开
        try:
            self._net_load_async()
        except Exception:
            pass

        try:
            self.lbl_chip.configure(
                text="◆ 管理员权限" if is_admin() else "◇ 标准权限")
        except Exception:
            pass
        self.cur_mode_idx_settings = None
        self._fill_apps()
        self._fill_modes()
        self._fill_dns()
        self._sys_refresh_state()
        self.set_status("已加载当前配置")

    def _fill_apps(self):
        self.tv_apps.delete(*self.tv_apps.get_children())
        for i, a in enumerate(self.apps_work):
            path = a.get("path", "")
            self.tv_apps.insert("", "end", text="",
                                values=(a.get("name", ""), path),
                                image=self._get_icon(path),
                                tags=("zebra",) if i % 2 else ())
        self._fill_pool()

    def _mode_label(self, m):
        """列表里显示模式名; 带显示/音量设置的模式加一个 ⚙ 标记(细节见右侧当前设置)。"""
        st = m.get("settings") or {}
        has = bool(st.get("volume") is not None or (st.get("display") or {}))
        return m["name"] + ("  ⚙" if has else "")

    def _fill_modes(self):
        self.lb_modes.delete(0, "end")
        n_global = len((self.global_mode_work or {}).get("apps") or [])
        self.lb_modes.insert("end", "🌍 %s (%d)" % (GLOBAL_MODE_NAME, n_global))
        for m in self.modes_work:
            self.lb_modes.insert("end", self._mode_label(m))
        self.lb_modes.selection_set(0)
        self._mode_selected()
        self._apply_mode_settings_row_visibility()

    def _mode_selected(self, ev=None):
        # 切走前先保存上一个模式正在编辑的设置
        if self.cur_mode_idx_settings is not None:
            self._mode_settings_save(quiet=True)
        sel = self.lb_modes.curselection()
        if not sel:
            self.cur_mode_idx_settings = None
            self._fill_mapps([])
            self.lbl_msettings.config(text="左侧选中一个模式, 即可保存它的音量 / 分辨率设置")
            return
        i = sel[0]
        if i == 0:
            # 全局模式: 不能单独存音量/显示设置, 它的应用会被所有模式带上
            self.cur_mode_idx_settings = None
            self._fill_mapps(list((self.global_mode_work or {}).get("apps") or []))
            # 全局模式只有应用列表, 不显示音量/分辨率设置行
            try:
                self.row3_settings.pack_forget()
                self.lbl_mset_hint.pack_forget()
            except Exception:
                pass
            self.lbl_msettings.config(
                text="🌍 全局模式: 这里的应用会被所有其他模式自动包含, "
                     "启动/切换模式时一起启动, 且不会被切换停止")
            return
        try:
            if not self.row3_settings.winfo_ismapped():
                self.row3_settings.pack(fill="x", pady=(0, 2), after=self.row2_settings)
            if not self.lbl_mset_hint.winfo_ismapped():
                self.lbl_mset_hint.pack(anchor="w", pady=(2, 0),
                                        after=self.row3_settings)
        except Exception:
            pass
        i -= 1
        if 0 <= i < len(self.modes_work):
            self.cur_mode_idx_settings = i
            self._fill_mapps(self.modes_work[i]["apps"])
            self._mode_settings_load(i)
        else:
            self.cur_mode_idx_settings = None
            self._fill_mapps([])

    @staticmethod
    def _msettings_summary(st):
        if not st:
            return "当前设置: (未保存)"
        bits = []
        if st.get("volume") is not None:
            bits.append("音量 %d%%" % st["volume"])
        d = st.get("display") or {}
        if d.get("recommended"):
            bits.append("显示 恢复推荐模式")
        elif d.get("w"):
            bits.append("显示 %s×%s @%sHz" % (d["w"], d["h"], d["hz"]))
        return "当前设置: " + ("、".join(bits) if bits else "(未保存)")

    def _mode_settings_load(self, i):
        st = self.modes_work[i].get("settings") or {}
        vol = st.get("volume")
        self.var_modevol.set("" if vol is None else str(vol))
        disp = st.get("display") or {}
        if disp.get("recommended"):
            self.var_modedisp.set("恢复推荐模式")
        elif disp.get("w"):
            try:
                label = fmt_mode((disp["w"], disp["h"], disp["hz"], 32))
                for lab in self.mode_disp_map:
                    if (self.mode_disp_map[lab]["w"], self.mode_disp_map[lab]["h"],
                            self.mode_disp_map[lab]["hz"]) == \
                            (disp["w"], disp["h"], disp["hz"]):
                        label = lab
                        break
                self.var_modedisp.set(label)
            except Exception:
                self.var_modedisp.set("不改显示")
        else:
            self.var_modedisp.set("不改显示")
        self.lbl_msettings.config(text=self._msettings_summary(st))

    def _mode_settings_save(self, quiet=False):
        i = self.cur_mode_idx_settings
        if i is None or not (0 <= i < len(self.modes_work)):
            return
        st = {}
        vol_txt = self.var_modevol.get().strip()
        if vol_txt and vol_txt.isdigit():
            st["volume"] = max(0, min(100, int(vol_txt)))
        disp_sel = self.var_modedisp.get()
        if disp_sel == "恢复推荐模式":
            st["display"] = {"recommended": True}
        elif disp_sel and disp_sel != "不改显示" and disp_sel in self.mode_disp_map:
            st["display"] = self.mode_disp_map[disp_sel]
        self.modes_work[i]["settings"] = st
        self.lbl_msettings.config(text=self._msettings_summary(st))
        # 只更新这一行的文字; 绝不能抢走用户当前的选中项, 否则新建模式无法切换选中
        try:
            new_label = self._mode_label(self.modes_work[i])
            k = i + 1          # 列表第 0 行固定是全局模式
            if self.lb_modes.get(k) != new_label:
                sel = list(self.lb_modes.curselection())
                self.lb_modes.unbind("<<ListboxSelect>>")
                try:
                    self.lb_modes.delete(k)
                    self.lb_modes.insert(k, new_label)
                    for s in sel:
                        self.lb_modes.selection_set(s)
                finally:
                    self.lb_modes.bind("<<ListboxSelect>>", self._mode_selected)
        except Exception:
            pass
        if not quiet:
            self.set_status("模式「%s」的设置已记入, 点【💾 保存并生效】写入配置" %
                            self.modes_work[i]["name"])

    def _cur_mode_idx(self):
        """当前选中的【普通模式】下标(全局模式返回 None)。"""
        sel = self.lb_modes.curselection()
        if not sel or sel[0] == 0:
            return None
        i = sel[0] - 1
        return i if 0 <= i < len(self.modes_work) else None

    def _apply_mode_settings_row_visibility(self):
        """全局模式选中时隐藏"音量/分辨率设置"整行, 普通模式显示。"""
        sel = self.lb_modes.curselection()
        is_global = (not sel) or sel[0] == 0
        try:
            if is_global:
                self.row3_settings.pack_forget()
                self.lbl_mset_hint.pack_forget()
            else:
                if not self.row3_settings.winfo_ismapped():
                    self.row3_settings.pack(fill="x", pady=(0, 2),
                                            after=self.row2_settings)
                if not self.lbl_mset_hint.winfo_ismapped():
                    self.lbl_mset_hint.pack(anchor="w", pady=(2, 0),
                                            after=self.row3_settings)
        except Exception:
            pass

    def _sel_mode_name(self):
        """当前选中项的名字(全局模式或普通模式)。"""
        sel = self.lb_modes.curselection()
        if not sel:
            return ""
        if sel[0] == 0:
            return "🌍 " + GLOBAL_MODE_NAME
        i = sel[0] - 1
        if 0 <= i < len(self.modes_work):
            return self.modes_work[i].get("name", "")
        return ""

    def _sel_apps(self):
        """当前选中项的应用列表(全局模式或普通模式); 未选中返回 None。"""
        sel = self.lb_modes.curselection()
        if not sel:
            return None
        if sel[0] == 0:
            return (self.global_mode_work or {}).get("apps")
        i = sel[0] - 1
        if 0 <= i < len(self.modes_work):
            return self.modes_work[i]["apps"]
        return None

    def _fill_mapps(self, apps):
        self.tv_mapps.delete(*self.tv_mapps.get_children())
        for i, e in enumerate(apps):
            path = e.get("path", "")
            self.tv_mapps.insert("", "end", text="",
                                 values=(e.get("name", ""), path, e.get("stop", "")),
                                 image=self._get_icon(path),
                                 tags=("zebra",) if i % 2 else ())

    def _fill_pool(self):
        """模式页应用库: 列出应用(带图标), 单击即可加入当前模式。
        受搜索框过滤; self._pool_rows 保存当前显示的行与 apps_work 的对应关系。"""
        try:
            self.tv_pool.delete(*self.tv_pool.get_children())
        except Exception:
            return
        try:
            q = (self.var_pool_search.get() or "").strip().lower()
        except Exception:
            q = ""
        rows = []
        for a in self.apps_work:
            name = a.get("name", "") or ""
            path = a.get("path", "") or ""
            if q and q not in name.lower() and q not in path.lower():
                continue
            rows.append(a)
        self._pool_rows = rows
        for i, a in enumerate(rows):
            path = a.get("path", "")
            self.tv_pool.insert("", "end", text="",
                                values=(a.get("name", ""), path),
                                image=self._get_icon(path),
                                tags=("zebra",) if i % 2 else ())
        try:
            if q:
                self.lbl_pool_count.configure(
                    text="匹配 %d / 共 %d 个" % (len(rows), len(self.apps_work)))
            else:
                self.lbl_pool_count.configure(
                    text="共 %d 个应用" % (len(self.apps_work),))
        except Exception:
            pass

    def _on_pool_search(self, *_a):
        """搜索框内容变化 → 重新过滤应用库。"""
        self._fill_pool()

    def _pool_pick(self, ev=None):
        try:
            sel = self.tv_pool.selection()
        except Exception:
            return
        if not sel:
            return
        j = self.tv_pool.index(sel[0])
        rows = getattr(self, "_pool_rows", None)
        if rows is None:
            rows = self.apps_work
        if not (0 <= j < len(rows)):
            return
        apps = self._sel_apps()
        if apps is None:
            self.set_status("⚠ 先在左侧选中一个模式, 再单击应用库里的图标")
            return
        a = rows[j]
        path = (a.get("path") or "").strip()
        if not path:
            return
        if any((e.get("path") or "").strip() == path for e in apps):
            self.set_status("「%s」已在该模式中" % (a.get("name") or path))
            return
        name = (a.get("name") or "").strip() or os.path.splitext(os.path.basename(path))[0]
        apps.append({"name": name, "path": path})
        self._fill_mapps(apps)
        try:
            self.tv_pool.selection_remove(sel[0])
        except Exception:
            pass
        mode_name = (self._sel_mode_name() or "模式")
        self.set_status("✅ 已把「%s」加入「%s」, 点【💾 保存并生效】" % (name, mode_name))

    def _fill_dns(self):
        self.tv_dns.delete(*self.tv_dns.get_children())
        for i, d in enumerate(self.dns_work):
            self.tv_dns.insert("", "end", values=(d["name"], ", ".join(d["servers"])),
                               tags=("zebra",) if i % 2 else ())

    # ---------- 交互
    def _pick_detected(self, ev=None):
        try:
            i = self.detected_names.index(self.var_pick.get())
            d = self.detected[i]
            self.var_name.set(str(d.get("name", "")))
            self.var_path.set(str(d.get("path", "")))
            self.set_status("已填入, 点【➕ 添加 / 更新 应用】")
        except ValueError:
            pass

    def _browse(self):
        from tkinter import filedialog
        p = filedialog.askopenfilename(
            parent=self.root, title="选择程序",
            filetypes=[("程序", "*.exe *.bat *.cmd *.lnk"), ("所有文件", "*.*")])
        if p:
            self.var_path.set(p)
            if not self.var_name.get().strip():
                self.var_name.set(os.path.splitext(os.path.basename(p))[0])

    def _browse_mode(self):
        from tkinter import filedialog
        p = filedialog.askopenfilename(
            parent=self.root, title="选择程序",
            filetypes=[("程序", "*.exe *.bat *.cmd *.lnk"), ("所有文件", "*.*")])
        if p:
            self.var_mpath.set(p)
            if not self.var_mname.get().strip():
                self.var_mname.set(os.path.splitext(os.path.basename(p))[0])

    def _app_fill(self, ev=None):
        sel = self.tv_apps.selection()
        if not sel:
            return
        i = self.tv_apps.index(sel[0])
        if 0 <= i < len(self.apps_work):
            a = self.apps_work[i]
            self.var_name.set(a.get("name", ""))
            self.var_path.set(a.get("path", ""))

    def _mapp_fill(self, ev=None):
        apps = self._sel_apps()
        sel = self.tv_mapps.selection()
        if apps is None or not sel:
            return
        j = self.tv_mapps.index(sel[0])
        if 0 <= j < len(apps):
            e = apps[j]
            self.var_mname.set(e.get("name", ""))
            self.var_mpath.set(e.get("path", ""))
            self.var_mstop.set(e.get("stop", ""))

    def _dns_fill(self, ev=None):
        sel = self.tv_dns.selection()
        if not sel:
            return
        i = self.tv_dns.index(sel[0])
        if 0 <= i < len(self.dns_work):
            d = self.dns_work[i]
            self.var_dnsname.set(d["name"])
            self.var_dnssrv.set(", ".join(d["servers"]))

    def _app_add(self):
        name = self.var_name.get().strip()
        path = self.var_path.get().strip()
        if not name or not path:
            self.set_status("⚠ 名称和路径都不能为空")
            return
        sel = self.tv_apps.selection()
        if sel:
            i = self.tv_apps.index(sel[0])
            if 0 <= i < len(self.apps_work):
                self.apps_work[i] = {"name": name, "path": path}
        else:
            self.apps_work.append({"name": name, "path": path})
        self._fill_apps()
        self.var_name.set("")
        self.var_path.set("")
        self.set_status("已改动, 点【💾 保存并生效】")

    def _app_del(self):
        sel = self.tv_apps.selection()
        if not sel:
            self.set_status("⚠ 先在列表里选中一行")
            return
        i = self.tv_apps.index(sel[0])
        if 0 <= i < len(self.apps_work):
            del self.apps_work[i]
        self._fill_apps()
        self.set_status("已删除, 点【💾 保存并生效】")

    def _mode_add(self):
        from tkinter import simpledialog
        name = simpledialog.askstring("添加模式", "模式名称:", parent=self.root)
        if name and name.strip():
            self.modes_work.append({"name": name.strip(), "apps": [], "settings": {}})
            self._fill_modes()
            self.lb_modes.selection_clear(0, "end")
            self.lb_modes.selection_set(len(self.modes_work))
            self._mode_selected()
            self.set_status("已添加模式【%s】, 点保存" % name.strip())

    def _mode_rename(self):
        from tkinter import simpledialog
        i = self._cur_mode_idx()
        if i is None:
            self.set_status("⚠ 选中左侧一个普通模式才能重命名 (全局模式不用改名)")
            return
        old = self.modes_work[i]["name"]
        name = simpledialog.askstring("重命名模式", "新名称:", initialvalue=old, parent=self.root)
        if name and name.strip():
            self.modes_work[i]["name"] = name.strip()
            self._fill_modes()
            self.lb_modes.selection_set(i + 1)
            self._mode_selected()
            self.set_status("已重命名, 点保存")

    def _mode_del(self):
        i = self._cur_mode_idx()
        if i is None:
            self.set_status("⚠ 全局模式不能删除 (选中一个普通模式再删)")
            return
        name = self.modes_work[i]["name"]
        self.cur_mode_idx_settings = None   # 防止把设置错存到被删除后顶位的模式
        self.modes_work.pop(i)
        self._fill_modes()
        self.set_status("已删除模式【%s】, 点保存" % name)

    def _mapp_add(self):
        apps = self._sel_apps()
        if apps is None:
            self.set_status("⚠ 先在左侧选中一个模式")
            return
        name = self.var_mname.get().strip()
        path = self.var_mpath.get().strip()
        stop = self.var_mstop.get().strip()
        if not name or not path:
            self.set_status("⚠ 名称和路径都不能为空")
            return
        entry = {"name": name, "path": path}
        if stop:
            entry["stop"] = stop
        sel = self.tv_mapps.selection()
        if sel:
            j = self.tv_mapps.index(sel[0])
            if 0 <= j < len(apps):
                apps[j] = entry
        else:
            apps.append(entry)
        self._mode_selected()
        self.var_mname.set("")
        self.var_mpath.set("")
        self.var_mstop.set("")
        self.set_status("已改动, 点【💾 保存并生效】")

    def _mapp_del(self):
        apps = self._sel_apps()
        sel = self.tv_mapps.selection()
        if apps is None or not sel:
            self.set_status("⚠ 先选中模式和应用行")
            return
        j = self.tv_mapps.index(sel[0])
        if 0 <= j < len(apps):
            apps.pop(j)
        self._mode_selected()
        self.set_status("已删除, 点【💾 保存并生效】")

    def _dns_add(self):
        name = self.var_dnsname.get().strip()
        srv = self.var_dnssrv.get().strip()
        if not name or not srv:
            self.set_status("⚠ 名称和服务器都不能为空")
            return
        servers = [s.strip() for s in srv.replace("，", ",").split(",") if s.strip()]
        if not servers:
            self.set_status("⚠ 服务器格式不对")
            return
        sel = self.tv_dns.selection()
        if sel:
            i = self.tv_dns.index(sel[0])
            if 0 <= i < len(self.dns_work):
                self.dns_work[i] = {"name": name, "servers": servers}
        else:
            self.dns_work.append({"name": name, "servers": servers})
        self._fill_dns()
        self.var_dnsname.set("")
        self.var_dnssrv.set("")
        self.set_status("已改动, 点【💾 保存并生效】")

    def _dns_del(self):
        sel = self.tv_dns.selection()
        if not sel:
            self.set_status("⚠ 先选中一行")
            return
        i = self.tv_dns.index(sel[0])
        if 0 <= i < len(self.dns_work):
            del self.dns_work[i]
        self._fill_dns()
        self.set_status("已删除, 点【💾 保存并生效】")

    # ---------- 保存
    def save(self):
        try:
            self._mode_settings_save(quiet=True)   # 先收起正在编辑的模式设置
            cfg = dict(self.app.config)
            cfg["apps"] = self.apps_work
            cfg["modes"] = self.modes_work
            cfg["proxy_server"] = self.var_proxy.get().strip()
            cfg["dns_presets"] = self.dns_work
            cfg["global_mode"] = {"apps": list(self.global_mode_work.get("apps") or [])}
            self.app.save_config(cfg)
            self.app.reload_config()
            self.set_status("✅ 已保存并生效 (托盘菜单已更新)")
        except Exception:
            log_exc()
            self.set_status("❌ 保存失败, 详见 data/log.txt")


# ---------------------------------------------------------------- 入口
def ensure_single_instance():
    h = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    err = ctypes.windll.kernel32.GetLastError()
    if err != 183:  # ERROR_ALREADY_EXISTS
        return h
    # 已在运行: 等旧实例退出 (可能正在提权重启), 3 秒后仍占用则退出
    for _ in range(6):
        time.sleep(0.5)
        h2 = ctypes.windll.kernel32.OpenMutexW(0x1F0001, False, MUTEX_NAME)
        if not h2:
            return h
        ctypes.windll.kernel32.CloseHandle(h2)
    sys.exit(0)


def selftest():
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    print("== QuickLauncher 自检 ==")
    print("admin:", is_admin())
    report, summary = do_detection()
    print("report:", report)
    print(summary)
    print("displays:")
    for name, label in enum_displays() or [("", "主显示器")]:
        cur = get_mode(name)
        modes = enum_modes(name)
        print("  %s (%s) 当前=%s 模式数=%d" % (label, name,
              fmt_mode(cur) if cur else "未知", len(modes)))
    print("wifi profiles:", wifi_profiles())
    print("OK")


def enable_dark_app_mode():
    """让 Windows 原生菜单/对话框跟随深色 (uxtheme 未文档化接口 135)。
    失败无副作用。"""
    try:
        import ctypes
        ux = ctypes.WinDLL("uxtheme.dll")
        set_mode = ux[135]
        set_mode.argtypes = [ctypes.c_int]
        set_mode.restype = ctypes.c_int
        set_mode(2)  # 2 = ForceDark
        log("dark app mode enabled")
    except Exception as e:
        log("dark app mode unavailable: %r" % (e,))


def main():
    try:
        from pystray import Icon, Menu, MenuItem
        from PIL import Image
    except Exception as e:
        ctypes.windll.user32.MessageBoxW(
            None, "缺少依赖库: %s\n\n请运行: pip install pystray pycaw" % e,
            APP_TITLE, 0x10)
        sys.exit(1)

    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

    ensure_single_instance()
    enable_dark_app_mode()

    img = make_icon_image()
    try:
        img.save(ICON_PATH, format="ICO",
                 sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64)])
    except Exception as e:
        log("icon save failed: %r" % (e,))

    app = QuickLauncher(None)
    icon = Icon("quicklauncher", img, APP_TITLE)
    icon.menu = app.build_menu()
    app.icon = icon
    log("started, admin=%s" % is_admin())
    icon.run(setup=app.on_ready)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
