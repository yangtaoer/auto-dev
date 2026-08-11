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

function Get-RunnerProcesses {
    @(
        Get-CimInstance Win32_Process | Where-Object {
            $_.CommandLine -and
            $_.CommandLine -match '(?i)(^|\s)-m\s+app\.local_runner_main(?:\s|$)'
        }
    )
}

function Get-RunnerListener {
    param([int]$Port)
    @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Task -and $Task.State -eq "Running") {
    Write-Host "正在停止计划任务 $TaskName ..."
    Stop-ScheduledTask -TaskName $TaskName
}

$StopDeadline = (Get-Date).AddSeconds(20)
do {
    $Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $Task -or $Task.State -ne "Running") {
        break
    }
    Start-Sleep -Milliseconds 250
} while ((Get-Date) -lt $StopDeadline)

# uv 启动器会派生真正的 Python 子进程，子进程命令行不一定包含项目路径。
# 按模块名停止整棵执行器进程，兼容 Codex/运行时更新后的启动方式。
$Processes = Get-RunnerProcesses
foreach ($Process in ($Processes | Sort-Object ProcessId -Descending)) {
    Stop-Process -Id $Process.ProcessId -Force -ErrorAction SilentlyContinue
}

$MonitorPort = Get-RunnerMonitorPort
$StopDeadline = (Get-Date).AddSeconds(15)
do {
    $Processes = Get-RunnerProcesses
    $Listeners = Get-RunnerListener -Port $MonitorPort
    if (-not $Processes -and -not $Listeners) {
        Write-Host "本机执行器已停止，端口 $MonitorPort 已释放。"
        exit 0
    }
    foreach ($Process in $Processes) {
        Stop-Process -Id $Process.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 250
} while ((Get-Date) -lt $StopDeadline)

$RemainingProcesses = Get-RunnerProcesses
$RemainingListeners = Get-RunnerListener -Port $MonitorPort
if ($RemainingProcesses -or $RemainingListeners) {
    $ProcessIds = @($RemainingProcesses.ProcessId) + @($RemainingListeners.OwningProcess) | Sort-Object -Unique
    throw "本机执行器未能完全停止，仍占用进程/端口：$($ProcessIds -join ', ')。"
}

Write-Host "本机执行器已停止。"
