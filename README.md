# 快捷启动台 QuickLauncher

一个常驻 Windows 托盘的「系统增强面板」：把常用的启动、网络、音量、分辨率操作收进一个小工具里，深色界面，双击即开。

纯 Python + 标准库实现，打包后是一个 26 MB 的绿色 exe，不需要安装 Python。

![Windows](https://img.shields.io/badge/Windows-10%20%2F%2011%20x64-0078D4)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB)
![License](https://img.shields.io/badge/license-MIT-green)

## 功能

| 模块 | 能做什么 |
| --- | --- |
| 🚀 应用列表 | 自动扫描开始菜单应用（排除系统内置），一键启动；支持手动添加、从检测库选择 |
| 🚀 自定义模式 | 一组应用打包成"模式"，一键启动/停止；模式可保存音量、分辨率设置，切换时自动应用；切换时**新模式也要用的应用保持运行**、**已在运行的直接跳过**；应用库支持搜索 |
| 🌐 网络设置 | WiFi（查看已保存配置、双击连接、改密码、自动连接、断开/删除）、以太网/网卡（看 IP/掩码/网关/DNS，直接改成静态或切回 DHCP，启停网卡）、代理开关、DNS 快捷切换 |
| 🔊 音量 | 拖动调整、一键静音、20/40/60/80/100% 预设 |
| 🖥 显示设置 | 列出显卡支持的所有分辨率×刷新率模式，一键切换、恢复推荐模式 |
| ⚙ 系统 | 开机自启、重新检测、打开检测报告、以管理员身份重启、退出 |

细节：

- **托盘图标**：双击 = 打开设置窗口，右键 = 快速切换模式（当前模式后面标 `✅ 当前`）
- **深色标题栏**：DWM 深色标题栏，没有白边；原生菜单也走深色
- **秒开**：窗口在后台预热，双击后通常 100~300 ms 内出现
- **不会弹黑窗**：所有 `netsh` / `tasklist` / `taskkill` 都在隐藏窗口执行，只有需要管理员权限的操作才弹 UAC
- **单实例**：重复启动会自动退出，不会出现两个图标

## 快速开始

### 方式 A：直接用 exe（推荐）

1. 到 [Releases](../../releases) 下载 `QuickLauncher.exe`（或自己打包，见下文）
2. 双击运行，托盘区出现 ⚡ 图标（Windows 11 可能藏在 `^` 里，拖到任务栏即可）
3. 首次启动会自动扫描本机应用，生成 `config.json` 和 `data\`

### 方式 B：安装脚本

1. 把 `QuickLauncher.exe`、`icon.ico`、`README.txt`、`install.*` / `uninstall.*` 放在同一目录
2. 双击 `install.bat`
   - 直接回车 = 装到 `%LOCALAPPDATA%\Programs\QuickLauncher`
   - 也可以输入自定义目录，例如 `D:\Apps\QuickLauncher`
   - 目录不可写会提示重输（最多 3 次），或右键「以管理员身份运行」
3. 自动创建桌面快捷方式，询问是否开机自启
4. 卸载：双击 `uninstall.bat`（安装路径记在 `HKCU\Software\QuickLauncher\InstallDir`，卸载时自动定位）

### 方式 C：源码运行

需要 Python 3.10+：

```bat
python -m pip install pystray pycaw comtypes pillow
python launcher.py
```

`start.bat` 会自动优先找 exe，找不到再找 pythonw / pyw / python。

## 打包 exe

```bat
python -m pip install pyinstaller
build.bat
```

`build.bat` 里就是这条命令：

```bat
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name QuickLauncher --icon icon.ico ^
  --hidden-import pystray._win32 ^
  --hidden-import comtypes --hidden-import comtypes.client ^
  --collect-submodules pycaw launcher.py
```

产物在 `dist\QuickLauncher.exe`。

> 打包时注意：正在运行的 exe 文件被锁定，覆盖前先退出程序；不要把 `dist\` 里的产物删了再复制。

## 配置文件

全部状态在同目录的 `config.json`（首次运行自动生成，参考 `config.example.json`）：

```json
{
  "apps": [
    {"name": "微信", "path": "C:\\Program Files\\Tencent\\WeChat\\WeChat.exe"}
  ],
  "apps_seeded": false,
  "current_mode": "工作模式",
  "proxy_server": "127.0.0.1:7890",
  "dns_presets": [
    {"name": "自动获取 (DHCP)", "servers": ["dhcp"]},
    {"name": "阿里 DNS (223.5.5.5)", "servers": ["223.5.5.5", "223.6.6.6"]}
  ],
  "modes": [
    {
      "name": "工作模式",
      "apps": [
        {"name": "VS Code", "path": "C:\\...\\Code.exe", "stop": "Code.exe"},
        {"name": "Chrome", "path": "C:\\...\\chrome.exe", "stop": "chrome.exe"}
      ],
      "settings": {"volume": 60, "display": {"w": 2560, "h": 1600, "hz": 240}}
    }
  ]
}
```

- `settings.display` 也可以写 `{"recommended": true}`（恢复推荐分辨率）
- `stop` 是进程映像名，用来精确停止「本次由本程序拉起」的进程；不填则自动取 `path` 的 exe 文件名
- 换电脑时应用路径会失效，按 exe 名仍能匹配同名程序；也可以在编辑窗口点【🔄 一键填充全部应用】重扫

运行时数据：`data\log.txt`（日志）、`data\reports\`（检测报告）、`data\icons\`（图标缓存）、`data\apps_detected.json`（应用检测结果）。

## 目录结构

```
launcher.py            主程序（托盘 + 编辑窗口，单文件）
icon.ico               图标
start.bat              启动（优先 exe，其次源码）
build.bat              打包 exe
install.bat/.ps1       安装脚本
uninstall.bat/.ps1     卸载脚本
config.example.json    配置模板
README.txt             面向普通用户的详细说明
tools/push_via_api.py  网络受限时用 GitHub API 推送（开发者工具）
```

## 网络/权限说明

- 查看 WiFi 密码、网卡信息 **不需要** 管理员权限（读 `netsh` 输出）
- 改 WiFi 密码、删除配置、改 IP/DNS、启停网卡 **需要** 管理员权限：程序会直接执行，失败时弹 UAC 确认框；也可以在系统页用【以管理员身份重启】避免反复弹窗
- 所有操作只改本机设置；DNS/代理只写当前用户的 `HKCU` 与网卡配置

## 关于本仓库

- 仓库里的 `config.json`、`data\`、`*.exe`、`release\` 都被 `.gitignore` 排除，不包含任何本机路径、日志或凭据
- 想在新电脑上更新：`git pull` 后直接跑 `python launcher.py`，或重新 `build.bat`
- 首次上传是通过 GitHub API 完成的（本机到 github.com:443 的连接被重置）。远端历史从两次 API 提交开始，如果以后网络恢复、想让本地 git 历史与远端一致：

```bat
git fetch origin main
git reset --hard origin/main
```

### 网络受限时如何推送

如果 `github.com:443` 连不上（`git push` 报 `Failed to connect`），但 `api.github.com` 能通，可以用：

```bat
python tools\push_via_api.py
```

它会读取凭据（`GITHUB_TOKEN` 环境变量 → Windows 凭据管理器 → `git credential`），通过 GitHub REST API 把当前目录的文件提交上去（自动新建仓库、自动生成 commit）。

## 许可

MIT
