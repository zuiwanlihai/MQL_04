# MT5 网关 Windows 安装包构建脚本（M17 · PRD 产品约束）
#
# 产物：dist\MT5GatewayInstaller-<version>.zip
#   MT5Gateway\MT5Gateway.exe   网关服务（内嵌 Python + 官方 MetaTrader5 包）
#   MT5Gateway\MT5Pair.exe      配对 CLI（一次性配对码 → 绑定后端）
#   MT5Gateway\.env.example     配置样例（复制为 .env 使用）
#   MT5Gateway\README.txt       安装步骤
#
# 用法（在仓库根目录，PowerShell）：
#   powershell -File gateway\installer\build.ps1          # 构建 zip
#   powershell -File gateway\installer\build.ps1 -Local   # 只在本机 dist\ 留目录不压缩
#
# 依赖：gateway\.venv 已装好依赖（含 MetaTrader5），PyInstaller 按需自动安装。

param(
    [switch]$Local
)

$ErrorActionPreference = "Stop"
$GatewayRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $GatewayRoot ".venv\Scripts\python.exe"
$WorkDir = Join-Path $PSScriptRoot "build"
$OutDir = Join-Path $WorkDir "MT5Gateway"

if (-not (Test-Path $VenvPython)) {
    Write-Host "未找到网关虚拟环境: $VenvPython" -ForegroundColor Red
    exit 1
}

# PyInstaller 按需安装（不污染 requirements）
& $VenvPython -m pip show pyinstaller *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "安装 PyInstaller …"
    & $VenvPython -m pip install pyinstaller
}

Write-Host "清理旧构建 …"
if (Test-Path $WorkDir) { Remove-Item $WorkDir -Recurse -Force }
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null

Push-Location $GatewayRoot
try {
    Write-Host "打包网关服务 MT5Gateway.exe …"
    & $VenvPython -m PyInstaller --noconfirm --clean --onedir --name MT5Gateway `
        --paths $GatewayRoot --distpath $WorkDir --workpath (Join-Path $WorkDir "_tmp_svc") `
        --hidden-import app.main --collect-all MetaTrader5 --collect-all numpy `
        --hidden-import uvicorn.logging --hidden-import uvicorn.loops.auto `
        --hidden-import uvicorn.protocols.http.auto --hidden-import uvicorn.protocols.websockets.auto `
        --hidden-import uvicorn.lifespan.on `
        (Join-Path $PSScriptRoot "gateway_entry.py")
    if ($LASTEXITCODE -ne 0) { throw "MT5Gateway 打包失败" }

    Write-Host "打包配对工具 MT5Pair.exe …"
    & $VenvPython -m PyInstaller --noconfirm --clean --onedir --name MT5Pair `
        --paths $GatewayRoot --distpath $WorkDir --workpath (Join-Path $WorkDir "_tmp_pair") `
        (Join-Path $PSScriptRoot "pair_entry.py")
    if ($LASTEXITCODE -ne 0) { throw "MT5Pair 打包失败" }
} finally {
    Pop-Location
}

# 汇聚产物：两个 onedir 合并到 MT5Gateway\（同名 _internal 冲突 → 保留服务版，配对 CLI 用目录内 exe 直接引用会缺依赖）
# 因此采用扁平结构：MT5Gateway\ 下放服务 exe 组，MT5Pair\ 独立目录。
Move-Item (Join-Path $WorkDir "MT5Pair") (Join-Path $OutDir "MT5Pair") -Force

# 配套文件
Copy-Item (Join-Path $GatewayRoot ".env.example") $OutDir -ErrorAction SilentlyContinue
Copy-Item (Join-Path $PSScriptRoot "README.txt") $OutDir -Force

if (-not $Local) {
    $version = (& $VenvPython -c "import tomllib; print(tomllib.load(open(r'$GatewayRoot\pyproject.toml','rb'))['project']['version'])")
    $zip = Join-Path $PSScriptRoot "MT5GatewayInstaller-$version.zip"
    if (Test-Path $zip) { Remove-Item $zip -Force }
    Compress-Archive -Path $OutDir -DestinationPath $zip
    Write-Host "构建完成: $zip" -ForegroundColor Green
} else {
    Write-Host "构建完成（本地目录）: $OutDir" -ForegroundColor Green
}
