# MT5 网关注册为 Windows 服务（M11 · PRD M1 服务常驻）
#
# 作用：开机自启 + 服务控制管理器保活（配合 app/watchdog 的双层常驻）。
# 用法（管理员 PowerShell）：
#   .\install_service.ps1            # 注册/更新服务
#   .\install_service.ps1 -Uninstall # 卸载
#
# 依赖 NSSM（Non-Sucking Service Manager，https://nssm.cc/download）：
#   将其解压到 gateway\deploy\nssm\ 或加入 PATH。
# NSSM 不可用时脚本打印 sc.exe 手工方案，不会误改系统。

param(
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$ServiceName = "MT5Gateway"
$GatewayRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $GatewayRoot ".venv\Scripts\python.exe"
$Nssm = Get-Command nssm.exe -ErrorAction SilentlyContinue
if (-not $Nssm) {
    $localNssm = Join-Path $PSScriptRoot "nssm\nssm.exe"
    if (Test-Path $localNssm) { $Nssm = $localNssm } else { $Nssm = $null }
}

if ($Uninstall) {
    if ($Nssm) {
        & $Nssm remove $ServiceName confirm | Out-Null
        Write-Host "服务 $ServiceName 已卸载" -ForegroundColor Green
    } else {
        Write-Host "未找到 nssm，无法自动卸载；请手工: sc delete $ServiceName" -ForegroundColor Yellow
    }
    exit 0
}

if (-not (Test-Path $VenvPython)) {
    Write-Host "未找到网关虚拟环境: $VenvPython （请先在 gateway\ 下创建 venv 并安装依赖）" -ForegroundColor Red
    exit 1
}

if ($Nssm) {
    & $Nssm install $ServiceName $VenvPython "-m app.watchdog" | Out-Null
    & $Nssm set $ServiceName AppDirectory $GatewayRoot | Out-Null
    & $Nssm set $ServiceName AppStdout (Join-Path $GatewayRoot ".runtime\watchdog.out.log") | Out-Null
    & $Nssm set $ServiceName AppStderr (Join-Path $GatewayRoot ".runtime\watchdog.err.log") | Out-Null
    & $Nssm set $ServiceName AppRestartDelay 5000 | Out-Null
    & $Nssm set $ServiceName Start SERVICE_AUTO_START | Out-Null
    & $Nssm start $ServiceName | Out-Null
    Write-Host "服务 $ServiceName 已注册并启动（开机自启，watchdog 常驻）" -ForegroundColor Green
} else {
    Write-Host @"
未找到 NSSM。手工方案（服务内跑控制台程序不可靠，建议安装 NSSM）：
  1) 下载 https://nssm.cc/download 解压到 gateway\deploy\nssm\
  2) 重新运行本脚本
或退而求其次：任务计划程序 → 创建任务 → 触发器「系统启动时」+「故障时重启」，
操作 = 启动程序：
  程序: $VenvPython
  参数: -m app.watchdog
  起始于: $GatewayRoot
"@ -ForegroundColor Yellow
}
