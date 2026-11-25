# 快捷启动台 卸载脚本 (PowerShell)
# 关程序 -> 删快捷方式 -> 删安装目录 -> 清注册表记录
param(
    [string]$Target = "",
    [switch]$Yes
)

$ErrorActionPreference = "Continue"

function Say($t) { Write-Host $t }

$regKey = "HKCU:\Software\QuickLauncher"

# 找到当初装到哪了: 参数 > 注册表记录 > 默认目录
if ([string]::IsNullOrWhiteSpace($Target)) {
    $saved = $null
    try {
        $saved = (Get-ItemProperty -Path $regKey -Name "InstallDir" -ErrorAction Stop).InstallDir
    } catch {
        $saved = $null
    }
    if (-not [string]::IsNullOrWhiteSpace($saved)) {
        $Target = $saved
    } else {
        $Target = Join-Path $env:LOCALAPPDATA "Programs\QuickLauncher"
        Say "没找到安装记录, 按默认目录处理。"
    }
}

$Target = $Target.Trim().Trim('"').Trim("'").Trim()
if ($Target -match '[\\/]$') { $Target = $Target.TrimEnd('\', '/') }

Say "安装目录: $Target"
if (-not (Test-Path $Target)) {
    Say "这个目录不存在, 可能已经卸载过了。"
}

if (-not $Yes) {
    $ans = Read-Host "确认卸载? 会删掉程序、配置和缓存 (输入 Y 继续)"
    if ($ans -notmatch "^[Yy]") { Say "已取消。"; exit 1 }
}

Get-Process -Name "QuickLauncher" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

$lnks = @(
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "快捷启动台.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Startup")) "快捷启动台.lnk")
)
foreach ($l in $lnks) {
    if (Test-Path $l) {
        Remove-Item $l -Force -ErrorAction SilentlyContinue
        Say "已删除快捷方式: $l"
    }
}

if (Test-Path $Target) {
    Remove-Item $Target -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path $Target) {
        Say "目录删不掉 (可能程序还在运行), 请注销后手动删除: $Target"
        exit 2
    }
    Say "已删除程序目录。"
}

Remove-Item $regKey -Recurse -Force -ErrorAction SilentlyContinue

Say "卸载完成。"
exit 0
