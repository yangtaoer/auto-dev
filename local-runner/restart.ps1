param([switch]$Force)

$ErrorActionPreference = "Stop"
$RunnerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$TaskName = "AutoDevLocalRunner"
$EnvFile = Join-Path $RunnerDir ".env.runner"

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

$MonitorPort = Get-RunnerMonitorPort
$HealthUri = "http://127.0.0.1:$MonitorPort/healthz"
try {
    $ExistingHealth = Invoke-RestMethod -Uri $HealthUri -TimeoutSec 2
}
catch {
    $ExistingHealth = $null
}
if (-not $Force -and $ExistingHealth -and (
    $ExistingHealth.state -eq "working" -or
    [int]($ExistingHealth.active_count) -gt 0 -or
    @($ExistingHealth.current_request_ids).Count -gt 0
)) {
    throw "执行器仍有 $($ExistingHealth.active_count) 个任务正在运行，为避免任务中断已拒绝重启。请等待任务结束；确需强制重启时使用 -Force。"
}

& (Join-Path $RunnerDir "stop.ps1")

$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $Task) {
    throw "未找到计划任务 $TaskName，请先运行 local-runner\install.ps1。"
}
Start-ScheduledTask -TaskName $TaskName
Write-Host "已启动计划任务，正在等待本机接口就绪..."

$StartDeadline = (Get-Date).AddSeconds(45)
$Health = $null
do {
    try {
        $Health = Invoke-RestMethod -Uri $HealthUri -TimeoutSec 2
    }
    catch {
        $Health = $null
    }
    if ($Health -and $Health.status -eq "ok") {
        break
    }
    Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $StartDeadline)

if (-not $Health -or $Health.status -ne "ok") {
    $Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    $Info = Get-ScheduledTaskInfo -TaskName $TaskName
    throw "执行器启动超时：本机接口 $HealthUri 未就绪；计划任务状态=$($Task.State)，结果=$($Info.LastTaskResult)。"
}

Write-Host "本机执行器已重新启动：PID $($Health.pid)，状态 $($Health.state)，端口 $MonitorPort。"
& (Join-Path $RunnerDir "status.ps1")
