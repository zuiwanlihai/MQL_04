# ============================================================
# 一键停止 start-all.ps1 拉起的所有服务（PostgreSQL 保留运行）
# ============================================================

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$RootDir     = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir  = Join-Path $RootDir "backend"
$GatewayDir  = Join-Path $RootDir "gateway"
$FrontendDir = Join-Path $RootDir "frontend"

Write-Host "`n▶ 停止 MT5 量化交易平台相关服务" -ForegroundColor Cyan

$targets = @(
    # 后端 uvicorn（子进程 python）
    @{ Name = "Backend (uvicorn)"; MatchPath = "uvicorn.exe"; MatchCmdLine = $BackendDir },
    # 网关 uvicorn
    @{ Name = "Gateway";           MatchPath = "uvicorn.exe"; MatchCmdLine = $GatewayDir },
    # 前端 vite 是 node 子进程
    @{ Name = "Frontend (vite)";   MatchPath = "node.exe";   MatchCmdLine = $FrontendDir }
)

$stopped = 0
$skipped = 0

# 方法：用 WMI Query 获取命令行再过滤，避免误伤系统其它 python/node
$allProcs = Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -or $_.Name }

foreach ($t in $targets) {
    $toKill = $null
    if ($t.MatchCmdLine) {
        # 将路径中的 \ 双倍化（命令行里是 \，但 -like 也是 \ 转义？这里直接简单处理）
        $escaped = $t.MatchCmdLine
        $toKill = $allProcs | Where-Object {
            $_.Name -eq $t.MatchPath -and
            $_.CommandLine -like "*$escaped*"
        }
    }

    if (-not $toKill) {
        Write-Host "   · $($t.Name) 未在运行" -ForegroundColor Gray
        $skipped++
        continue
    }

    foreach ($p in $toKill) {
        try {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
            Write-Host "   ✓ 已停止 $($t.Name) (PID=$($p.ProcessId))" -ForegroundColor Green
            $stopped++
        } catch {
            Write-Host "   ⚠ 停止失败 PID=$($p.ProcessId): $_" -ForegroundColor Yellow
        }
    }
}

Write-Host "`n▶ 顺手清理后端子进程（reload 模式会派生）" -ForegroundColor Gray
Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "python.exe" -and $_.CommandLine -like "*$BackendDir*"
} | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "   ✓ 已停止 backend python 子进程 PID=$($_.ProcessId)" -ForegroundColor Green
}

Write-Host "`n完成：停止 $stopped 个 / 跳过 $skipped 个。PostgreSQL 未停止（需关库另操作）。" -ForegroundColor Green
