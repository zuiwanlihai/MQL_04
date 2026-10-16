# ============================================================
# MT5 量化交易平台 — 一键启动（Windows PowerShell）
# 用法：在项目根目录直接运行  .\start-all.ps1
# 前置：
#   1. 便携 PG 数据目录  .tools\pgdata  存在
#   2. backend\.venv 与 gateway\.venv 已装好（后者含 MetaTrader5）
#   3. MT5 终端  E:\SRE\mql\MT5_Platform\terminal64.exe 已启动并登录
#   4. 前端 node_modules 存在（首次或依赖变更时会自动 npm install）
# ============================================================

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# -------- 路径常量（全部基于脚本所在目录，避免相对路径踩坑）--------
$RootDir      = Split-Path -Parent $MyInvocation.MyCommand.Path
$PGBin        = Join-Path $RootDir ".tools\pgsql\bin\pg_ctl.exe"
$PGData       = Join-Path $RootDir ".tools\pgdata"
$BackendDir   = Join-Path $RootDir "backend"
$GatewayDir   = Join-Path $RootDir "gateway"
$FrontendDir  = Join-Path $RootDir "frontend"
$BackendVenv  = Join-Path $BackendDir  ".venv\Scripts"
$GatewayVenv  = Join-Path $GatewayDir  ".venv\Scripts"
$LogDir       = Join-Path $RootDir "logs"

$BackendPort  = 8000
$GatewayPort  = 8090
$FrontendPort = 3000

# -------- 工具函数 --------
function Write-Step($msg)  { Write-Host "`n▶ $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "   ✓ $msg" -ForegroundColor Green }
function Write-Warn($msg)  { Write-Host "   ⚠ $msg" -ForegroundColor Yellow }
function Write-Fail($msg)  { Write-Host "   ✗ $msg" -ForegroundColor Red }

function Test-Port($port) {
    $r = Test-NetConnection -ComputerName 127.0.0.1 -Port $port -InformationLevel Quiet -WarningAction SilentlyContinue -ErrorAction SilentlyContinue
    return [bool]$r
}
function Wait-Port($port, $timeoutSec = 30, $label = "port") {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-Port $port) { return $true }
        Start-Sleep -Milliseconds 500
    }
    Write-Fail "$label ($port) 在 ${timeoutSec}s 内未就绪"
    return $false
}

function Start-ServiceWindow($title, $workDir, $cmd) {
    # 每个服务独立 PowerShell 窗口，-NoExit 便于实时看日志/手动 Ctrl+C 停止
    $logFile = Join-Path $LogDir ($title + ".log")
    $argList = @(
        "-NoExit"
        , "-Command"
        , "`$Host.UI.RawUI.WindowTitle = '$title'; Set-Location '$workDir'; Write-Host '===== $title =====' -ForegroundColor Magenta; Write-Host ('目录: $workDir'); Write-Host ('时间: ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')); $cmd 2>&1 | Tee-Object -FilePath '$logFile'"
    )
    $p = Start-Process -FilePath "powershell.exe" -ArgumentList $argList -WorkingDirectory $workDir -PassThru
    Write-Ok "$title 已启动 (PID=$($p.Id))"
    return $p
}

# -------- 1. 前置检查 --------
Write-Step "前置检查"

$ok = $true
if (-not (Test-Path $PGBin))       { Write-Fail "PostgreSQL 二进制不存在: $PGBin"; $ok = $false }
if (-not (Test-Path $PGData))      { Write-Fail "PostgreSQL 数据目录不存在: $PGData"; $ok = $false }
if (-not (Test-Path $BackendVenv)) { Write-Fail "backend\.venv 不存在，请先创建"; $ok = $false }
if (-not (Test-Path $GatewayVenv)) { Write-Fail "gateway\.venv 不存在，请先创建"; $ok = $false }
if (-not (Test-Path $FrontendDir)) { Write-Fail "frontend 目录不存在"; $ok = $false }
if (-not (Test-Path "$GatewayVenv\python.exe")) { Write-Fail "gateway\.venv\Scripts\python.exe 不存在"; $ok = $false }
if (-not (Test-Path "$BackendVenv\python.exe")) { Write-Fail "backend\.venv\Scripts\python.exe 不存在"; $ok = $false }

if (-not $ok) {
    Write-Host "`n前置条件不满足，退出。" -ForegroundColor Red
    exit 1
}
Write-Ok "路径检查通过"

# -------- 2. 日志目录 --------
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

# -------- 3. PostgreSQL --------
Write-Step "PostgreSQL (端口 5432)"
& $PGBin status -D $PGData | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Ok "PostgreSQL 已在运行"
} else {
    Write-Host "   启动中..." -ForegroundColor Gray
    & $PGBin start -D $PGData -l (Join-Path $LogDir "postgres.log") -w
    if ($LASTEXITCODE -ne 0) { Write-Fail "PostgreSQL 启动失败，查看 logs\postgres.log"; exit 1 }
    if (-not (Wait-Port 5432 20 "PostgreSQL")) { exit 1 }
    Write-Ok "PostgreSQL 已就绪 (端口 5432)"
}

# -------- 4. Backend --------
Write-Step "Backend FastAPI (端口 $BackendPort)"
if (Test-Port $BackendPort) {
    Write-Warn "端口 $BackendPort 已被占用，跳过启动（若需重启请先 stop-all.ps1）"
} else {
    # 首次或迁移变更时自动跑 alembic upgrade head
    $migrateLog = Join-Path $LogDir "backend-migrate.log"
    Write-Host "   执行数据库迁移 alembic upgrade head ..." -ForegroundColor Gray
    Push-Location $BackendDir
    # alembic 把 INFO 日志写到 stderr，PowerShell Strict 模式会把它当异常炸掉；
    # 这里临时放松 ErrorAction 并将 stderr 合并进 stdout 统一收集
    $prevEA = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & (Join-Path $BackendVenv "python.exe") -m alembic upgrade head 2>&1 | Tee-Object -FilePath $migrateLog | Out-Null
    $migrateOk = $LASTEXITCODE
    $ErrorActionPreference = $prevEA
    Pop-Location
    if ($migrateOk -ne 0) { Write-Warn "alembic 退出码=$migrateOk（可能是重复迁移，继续尝试启动）" }

    Start-ServiceWindow "Backend" $BackendDir @"
        Set-Location '$BackendDir'
        # 默认不带 --reload：watcher 会被 alembic / .pyc 文件触发假热重载导致进程崩掉。
        # 开发时若需要热重载，把 --reload 加回来即可。
        & '$BackendVenv\uvicorn.exe' app.main:app --host 127.0.0.1 --port $BackendPort
"@

    if (-not (Wait-Port $BackendPort 45 "Backend")) { Write-Warn "Backend 启动超时，窗口仍在运行，手动检查吧" }
    else { Write-Ok "Backend 已就绪  http://127.0.0.1:$BackendPort/docs" }
}

# -------- 5. Gateway --------
Write-Step "MT5 Gateway (端口 $GatewayPort)"
if (Test-Port $GatewayPort) {
    Write-Warn "端口 $GatewayPort 已被占用，跳过启动"
} else {
    # 检查 MT5 终端是否在运行（网关会自检，但提前提示更友好）
    $mt5Proc = Get-Process terminal64 -ErrorAction SilentlyContinue
    if (-not $mt5Proc) {
        Write-Warn "terminal64.exe 未检测到，Gateway 启动后会因 MT5 IPC 不可达而拒绝启动"
        Write-Warn "请先手动启动 MT5 终端并登录 Broker 账户，然后手动重试 Gateway"
    }

    Start-ServiceWindow "Gateway" $GatewayDir @"
        Set-Location '$GatewayDir'
        & '$GatewayVenv\uvicorn.exe' app.main:app --host 127.0.0.1 --port $GatewayPort
"@

    if (-not (Wait-Port $GatewayPort 60 "Gateway")) { Write-Warn "Gateway 启动超时，检查是否 MT5 终端未登录" }
    else { Write-Ok "Gateway 已就绪" }
}

# -------- 6. Frontend --------
Write-Step "Frontend Vite (端口 $FrontendPort)"
$NodeModules = Join-Path $FrontendDir "node_modules"
if (-not (Test-Path $NodeModules)) {
    Write-Host "   node_modules 不存在，执行 npm install ..." -ForegroundColor Gray
    Push-Location $FrontendDir
    npm install *>&1 | Tee-Object -FilePath (Join-Path $LogDir "npm-install.log")
    Pop-Location
    if (-not (Test-Path $NodeModules)) { Write-Fail "npm install 失败"; exit 1 }
    Write-Ok "npm install 完成"
}

if (Test-Port $FrontendPort) {
    Write-Warn "端口 $FrontendPort 已被占用，跳过启动"
} else {
    Start-ServiceWindow "Frontend" $FrontendDir @"
        Set-Location '$FrontendDir'
        npm run dev
"@

    if (-not (Wait-Port $FrontendPort 45 "Frontend")) { Write-Warn "Frontend 启动超时" }
    else { Write-Ok "Frontend 已就绪  http://localhost:$FrontendPort" }
}

# -------- 收尾 --------
Write-Host @"

╔═══════════════════════════════════════════════════════════════╗
║  所有服务已拉起，每个在独立 PowerShell 窗口中运行              ║
║  关闭某窗口 → 对应服务停止；或者用 .\stop-all.ps1 一键全停    ║
╠═══════════════════════════════════════════════════════════════╣
║  前端       http://localhost:$FrontendPort                     ║
║  后端 API   http://127.0.0.1:$BackendPort/docs                 ║
║  网关       http://127.0.0.1:$GatewayPort                      ║
╚═══════════════════════════════════════════════════════════════╝

"@ -ForegroundColor Green
