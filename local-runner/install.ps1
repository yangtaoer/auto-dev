param(
    [switch]$RegisterStartupTask
)

$ErrorActionPreference = "Stop"
$RunnerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $RunnerDir
$RunnerPython = Join-Path $ProjectRoot ".venv-runner\Scripts\python.exe"
$SharedPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Python = $RunnerPython
$EnvFile = Join-Path $RunnerDir ".env.runner"
$SecretsDir = Join-Path $RunnerDir "secrets"

if (-not (Test-Path -LiteralPath $RunnerPython) -and (Test-Path -LiteralPath $SharedPython)) {
    & $SharedPython -c "import httpx, openai_codex" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $Python = $SharedPython
        Write-Host "检测到可用的现有 Codex Python 环境，直接复用。"
    }
}

if (-not (Test-Path -LiteralPath $Python)) {
    $Candidates = @(
        @{ Command = "py"; Prefix = @("-3.12") },
        @{ Command = "py"; Prefix = @("-3") },
        @{ Command = "python"; Prefix = @() }
    )
    $Created = $false
    foreach ($Candidate in $Candidates) {
        if (-not (Get-Command $Candidate.Command -ErrorAction SilentlyContinue)) { continue }
        try {
            & $Candidate.Command @($Candidate.Prefix) -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
            if ($LASTEXITCODE -ne 0) { continue }
            & $Candidate.Command @($Candidate.Prefix) -m venv (Join-Path $ProjectRoot ".venv-runner")
            if ($LASTEXITCODE -eq 0) {
                $Created = $true
                $Python = $RunnerPython
                break
            }
        }
        catch { continue }
    }
    if (-not $Created) {
        throw "未找到可用的 Python 3.11+ x64，请先安装 Python 后重试。"
    }
}
if ($Python -eq $RunnerPython) {
    & $Python -m pip install --upgrade pip
    & $Python -m pip install -r (Join-Path $ProjectRoot "requirements-runner.txt")
}

New-Item -ItemType Directory -Force -Path $SecretsDir | Out-Null
if (-not (Test-Path -LiteralPath $EnvFile)) {
    Copy-Item -LiteralPath (Join-Path $RunnerDir ".env.runner.example") -Destination $EnvFile
}
foreach ($Name in @("runner-token.txt", "tfs-pat.txt", "tfs-reviewer-pat.txt", "codex-api-key.txt", "aliyun-access-key-id.txt", "aliyun-access-key-secret.txt")) {
    $Target = Join-Path $SecretsDir $Name
    if (-not (Test-Path -LiteralPath $Target)) {
        New-Item -ItemType File -Path $Target | Out-Null
    }
}

if ($RegisterStartupTask) {
    $Identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $WindowsPrincipal = New-Object System.Security.Principal.WindowsPrincipal($Identity)
    if (-not $WindowsPrincipal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "注册开机自启需要管理员权限，请运行 local-runner\install-startup-task.ps1 并确认 Windows UAC 提示。"
    }
    $TaskName = "AutoDevLocalRunner"
    $StartScript = Join-Path $RunnerDir "task-entry.ps1"
    $Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$StartScript`""
    $Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $Arguments -WorkingDirectory $ProjectRoot
    $CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $Triggers = @(
        (New-ScheduledTaskTrigger -AtStartup),
        (New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser),
        (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650))
    )
    $Settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -DontStopOnIdleEnd `
        -WakeToRun `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -MultipleInstances IgnoreNew `
        -RestartCount 99 `
        -RestartInterval (New-TimeSpan -Minutes 1)
    $Principal = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Triggers -Settings $Settings -Principal $Principal -Description "AutoDev 本机 DevCore 执行器（开机、登录自动启动，失败定时恢复）" -Force | Out-Null
    Write-Host "已注册开机自启任务：$TaskName（开机/登录触发，异常退出后每 5 分钟兜底恢复）"
}

& (Join-Path $RunnerDir "install-client-shortcut.ps1")

Write-Host "本机执行器安装完成。"
Write-Host "1. 编辑 $EnvFile"
Write-Host "2. 将云端 runner_token.txt 内容写入 $SecretsDir\runner-token.txt"
Write-Host "3. 写入 TFS PAT；Codex 默认复用本机登录态"
Write-Host "4. 运行 $RunnerDir\start.ps1"
Write-Host "图形控制台：双击桌面的“AutoDev 执行器控制台”，或运行 client.ps1"
Write-Host "管理命令：client.ps1 / status.ps1 / logs.ps1 -Follow / stop.ps1 / restart.ps1"
