$ErrorActionPreference = "Stop"
$RunnerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $RunnerDir
$EnvFile = Join-Path $RunnerDir ".env.runner"
$RunnerPythonw = Join-Path $ProjectRoot ".venv-runner\Scripts\pythonw.exe"
$SharedPythonw = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"

if (-not (Test-Path -LiteralPath $EnvFile)) {
    throw "缺少 $EnvFile，请先运行 local-runner\install.ps1 并完成配置。"
}
if (Test-Path -LiteralPath $RunnerPythonw) {
    $Pythonw = $RunnerPythonw
}
elseif (Test-Path -LiteralPath $SharedPythonw) {
    $Pythonw = $SharedPythonw
}
else {
    throw "缺少本机执行器 Python 环境，请先运行 local-runner\install.ps1。"
}

$env:AUTODEV_ENV_FILE = $EnvFile
Start-Process -FilePath $Pythonw -ArgumentList "-m", "app.local_client" -WorkingDirectory $ProjectRoot
