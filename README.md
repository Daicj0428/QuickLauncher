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
| 🚀 自定义模式 | 一组应用打包成"模式"，一键启动/停止；模式可保存音量、分辨率设置，切换时自动应用；**切换时自动停止不属于本模式的应用**；第一行固定的**🌍 全局模式**里的应用会被所有模式自动包含、切换时永不停止；本模式要用的保持运行，已在运行的直接跳过；应用库支持搜索；音量/显示设置也在这里（去掉独立页签后统一在此控制） |
| 🌐 网络设置 | WiFi（顶部「WiFi 无线开关」一键开关 WiFi，通过启用/禁用无线网卡实现；主区「📡 附近 WiFi」扫描当前能搜到的所有网络，未保存的双击即连；「💾 已保存的 WiFi 配置」折叠区里查看已保存配置、双击连接、改密码、自动连接、断开/删除）、以太网/网卡（看 IP/掩码/网关/DNS，直接改成静态或切回 DHCP，启停网卡）、代理开关、DNS 快捷切换 |
| ⚙ 系统 | **默认展示本机全部系统信息**（操作系统、处理器、显卡、内存、存储空间等，可刷新）；下方**「当前模式应用资源」表格**以任务管理器口径统计当前模式（全局模式 + 当前模式）各应用的 应用/状态/进程/CPU/内存/磁盘/GPU 七列（表格内容居中；GPU 显示使用率百分比；网络速率以整机「↓下载 · ↑上传」单独显示在表格上方，因 Windows 无法按进程取网络字节数）（可勾选「⏱ 动态刷新」每 5 秒自动重测）；开机自启、重新检测、打开检测报告、以管理员身份重启、退出 |

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
  ],
  "global_mode": {
    "apps": [{"name": "微信", "path": "C:\...\WeChat.exe", "stop": "WeChat.exe"}]
  }
}
```

- `settings.display` 也可以写 `{"recommended": true}`（恢复推荐分辨率）
- `global_mode.apps` 是**全局模式**的应用：每个模式都会自动带上它们（启动时一起启动、切换时永不停止），编辑窗口「自定义模式」页第一行固定就是它，可当普通模式一样增删应用（全局模式只有应用，不含音量/分辨率设置）
- 旧版 `global_keep`（exe 文件名数组）仍然兼容，但推荐改用 `global_mode`
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
```

## 网络/权限说明

- 查看 WiFi 密码、网卡信息 **不需要** 管理员权限（读 `netsh` 输出）
- 改 WiFi 密码、删除配置、改 IP/DNS、启停网卡 **需要** 管理员权限：程序会直接执行，失败时弹 UAC 确认框；也可以在系统页用【以管理员身份重启】避免反复弹窗
- 所有操作只改本机设置；DNS/代理只写当前用户的 `HKCU` 与网卡配置

## 关于本仓库

- 仓库里的 `config.json`、`data\`、`*.exe`、`release\` 都被 `.gitignore` 排除，不包含任何本机路径、日志或凭据
- 想在新电脑上更新：`git pull` 后直接跑 `python launcher.py`，或重新 `build.bat`

## 许可

MIT
