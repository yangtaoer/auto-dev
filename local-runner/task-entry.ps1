$ErrorActionPreference = "Stop"
$RunnerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $RunnerDir
$RunnerPython = Join-Path $ProjectRoot ".venv-runner\Scripts\python.exe"
$SharedPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$StartupLog = Join-Path $ProjectRoot "data\runner\logs\startup.log"

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $StartupLog) | Out-Null
try {
    if (Test-Path -LiteralPath $RunnerPython) {
        $Python = $RunnerPython
    }
    elseif (Test-Path -LiteralPath $SharedPython) {
        $Python = $SharedPython
    }
    else {
        throw "未找到 Runner Python 环境"
    }

    "$(Get-Date -Format o) task start python=$Python" | Add-Content -LiteralPath $StartupLog -Encoding utf8
    $env:AUTODEV_ENV_FILE = Join-Path $RunnerDir ".env.runner"
    Set-Location -LiteralPath $ProjectRoot
    & $Python -m app.local_runner_main
    $ExitCode = $LASTEXITCODE
    "$(Get-Date -Format o) runner exit code=$ExitCode" | Add-Content -LiteralPath $StartupLog -Encoding utf8
    exit $ExitCode
}
catch {
    "$(Get-Date -Format o) task failed: $($_.Exception.Message)" | Add-Content -LiteralPath $StartupLog -Encoding utf8
    exit 1
}
