# 快捷启动台 安装脚本 (PowerShell)
# 装到当前用户目录 (可自定义路径), 建桌面快捷方式, 可选开机自启。默认不需要管理员权限。
param(
    [string]$Target = "",
    [switch]$NoShortcut,
    [switch]$AutoStart,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"

function Say($t) { Write-Host $t }

$src = $PSScriptRoot
$exeName = "QuickLauncher.exe"
$srcExe = Join-Path $src $exeName
$defaultDir = Join-Path $env:LOCALAPPDATA "Programs\QuickLauncher"
$regKey = "HKCU:\Software\QuickLauncher"

if (-not (Test-Path $srcExe)) {
    Say "找不到 QuickLauncher.exe，请把安装包解压后整个文件夹一起运行。"
    exit 2
}

function Normalize([string]$p) {
    $p = $p.Trim()
    $p = $p.Trim('"').Trim("'").Trim()
    if ([string]::IsNullOrWhiteSpace($p)) { return "" }
    if ($p -eq "." ) { return (Get-Location).Path }
    return [System.IO.Path]::GetFullPath($p)
}

function Test-Writable([string]$dir) {
    try {
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
        $probe = Join-Path $dir ".ql_write_test"
        Set-Content -Path $probe -Value "x" -ErrorAction Stop
        Remove-Item $probe -Force -ErrorAction SilentlyContinue
        return $true
    } catch {
        return $false
    }
}

# ---------- 1. 选安装目录 ----------
if (-not [string]::IsNullOrWhiteSpace($Target)) {
    $Target = Normalize $Target
} elseif ($Yes) {
    $Target = $defaultDir
}

if ([string]::IsNullOrWhiteSpace($Target)) {
    $try = 0
    while ($true) {
        $try++
        Say ""
        Say "默认安装目录: $defaultDir"
        Say "直接回车 = 用默认目录; 也可以输入自定义路径 (例如 D:\Apps\QuickLauncher)"
        $ans = Read-Host "安装到"
        $cand = Normalize $ans
        if ([string]::IsNullOrWhiteSpace($cand)) { $cand = $defaultDir }

        if (Test-Writable $cand) { $Target = $cand; break }

        Say ""
        Say "!! 这个目录不能用 (没有写入权限或路径非法): $cand"
        if ($try -ge 3) {
            Say "换个目录再试, 或者右键安装脚本选 以管理员身份运行。"
            exit 3
        }
    }
} elseif (-not (Test-Writable $Target)) {
    Say "目录不可写: $Target"
    Say "请换一个目录, 或以管理员身份运行安装脚本。"
    exit 3
}

if ($Target -match '[\\/]$') { $Target = $Target.TrimEnd('\', '/') }

Say ""
Say "安装目录: $Target"
New-Item -ItemType Directory -Force -Path $Target | Out-Null

# ---------- 2. 复制文件 (保留已有 config.json / data) ----------
Copy-Item $srcExe $Target -Force
foreach ($f in @("icon.ico", "README.txt")) {
    $p = Join-Path $src $f
    if (Test-Path $p) { Copy-Item $p $Target -Force }
}
Say "已复制程序文件。"

# 记下安装位置, 卸载脚本据此找到目录
New-Item -Path $regKey -Force | Out-Null
Set-ItemProperty -Path $regKey -Name "InstallDir" -Value $Target

# ---------- 3. 桌面快捷方式 ----------
if (-not $NoShortcut) {
    try {
        $desktop = [Environment]::GetFolderPath("Desktop")
        $lnk = Join-Path $desktop "快捷启动台.lnk"
        $ws = New-Object -ComObject WScript.Shell
        $sc = $ws.CreateShortcut($lnk)
        $sc.TargetPath = Join-Path $Target $exeName
        $sc.WorkingDirectory = $Target
        $sc.IconLocation = (Join-Path $Target $exeName) + ",0"
        $sc.Description = "快捷启动台 - 应用快捷启动 / 网络 / 音量 / 显示"
        $sc.Save()
        Say "已创建桌面快捷方式: 快捷启动台"
    } catch {
        Say "桌面快捷方式创建失败 (不影响使用): $($_.Exception.Message)"
    }
}

# ---------- 4. 开机自启 (可选) ----------
$askAutostart = -not $Yes
if ($AutoStart) { $askAutostart = $false }
if ($askAutostart) {
    Say ""
    $ans = Read-Host "要不要开机自动启动? 输入 Y 开启, 直接回车跳过"
    if ($ans -match "^[Yy]") { $AutoStart = $true }
}
if ($AutoStart) {
    try {
        $startupDir = [Environment]::GetFolderPath("Startup")
        $startup = Join-Path $startupDir "快捷启动台.lnk"
        $ws = New-Object -ComObject WScript.Shell
        $sc = $ws.CreateShortcut($startup)
        $sc.TargetPath = Join-Path $Target $exeName
        $sc.WorkingDirectory = $Target
        $sc.IconLocation = (Join-Path $Target $exeName) + ",0"
        $sc.Description = "快捷启动台 (开机自启)"
        $sc.Save()
        Say "已设置开机自启。"
    } catch {
        Say "开机自启设置失败 (不影响使用): $($_.Exception.Message)"
    }
}

# ---------- 5. 启动 ----------
Say ""
Say "正在启动快捷启动台 ..."
Start-Process -FilePath (Join-Path $Target $exeName) -WorkingDirectory $Target
Start-Sleep -Seconds 2

Say ""
Say "说明:"
Say "  - 托盘区会出现蓝色闪电图标 (可能藏在 ^ 里, 拖到任务栏即可常驻)。"
Say "  - 双击图标 = 打开设置窗口; 右键 = 快速切换模式。"
Say "  - 首次启动会自动扫描本机的应用, 稍等几秒。"
Say "  - 程序、配置、缓存都在: $Target"
Say "    整个文件夹拷走即可迁移到另一台电脑。"
if (-not $AutoStart) {
    Say "  - 想开机自启: 设置窗口 - 系统页 - 开机自启, 或重新运行安装脚本。"
}
Say "  - 卸载: 双击 uninstall.bat (会自动找到这个目录)。"
exit 0
