$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "未找到虚拟环境。请先按 README 执行安装命令。"
}

Set-Location $root
& $python -m app.main
