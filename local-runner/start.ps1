$ErrorActionPreference = "Stop"
$RunnerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $RunnerDir
$EnvFile = Join-Path $RunnerDir ".env.runner"
$RunnerPython = Join-Path $ProjectRoot ".venv-runner\Scripts\python.exe"
$SharedPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

function Get-RunnerMonitorPort {
    $DefaultPort = 28766
    if (-not (Test-Path -LiteralPath $EnvFile)) {
        return $DefaultPort
    }
    $Match = Get-Content -LiteralPath $EnvFile | Where-Object {
        $_ -match '^\s*AUTODEV_RUNNER_MONITOR_PORT\s*='
    } | Select-Object -Last 1
    if ($Match -and $Match -match '=\s*["'']?(\d+)["'']?\s*$') {
        return [int]$Matches[1]
    }
    return $DefaultPort
}

if (-not (Test-Path -LiteralPath $EnvFile)) {
    throw "缺少 $EnvFile，请先运行 local-runner\install.ps1 并完成配置。"
}
if (Test-Path -LiteralPath $RunnerPython) {
    $Python = $RunnerPython
}
elseif (Test-Path -LiteralPath $SharedPython) {
    & $SharedPython -c "import httpx, openai_codex" 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "共享 Python 环境缺少 Runner 依赖，请先运行 local-runner\install.ps1。"
    }
    $Python = $SharedPython
}
else {
    throw "缺少本机执行器虚拟环境，请先运行 local-runner\install.ps1。"
}

$MonitorPort = Get-RunnerMonitorPort
try {
    $ExistingHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$MonitorPort/healthz" -TimeoutSec 2
}
catch {
    $ExistingHealth = $null
}
if ($ExistingHealth -and $ExistingHealth.status -eq "ok") {
    Write-Host "本机执行器已经在线：PID $($ExistingHealth.pid)，端口 $MonitorPort。"
    exit 0
}

$env:AUTODEV_ENV_FILE = $EnvFile
Set-Location -LiteralPath $ProjectRoot
& $Python -m app.local_runner_main
exit $LASTEXITCODE
