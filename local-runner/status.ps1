$ErrorActionPreference = "Stop"
$RunnerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $RunnerDir))
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

$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Task) {
    $Info = Get-ScheduledTaskInfo -TaskName $TaskName
    [pscustomobject]@{
        ScheduledTask = $Task.TaskName
        TaskState = $Task.State
        LastRunTime = $Info.LastRunTime
        LastTaskResult = $Info.LastTaskResult
    } | Format-List
}
else {
    Write-Host "计划任务：未注册（仍可手工或后台运行）"
}

$Processes = @(
    Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and
        $_.CommandLine -match '(?i)(^|\s)-m\s+app\.local_runner_main(?:\s|$)'
    }
)
if ($Processes) {
    $Processes | Select-Object ProcessId, ParentProcessId, Name, CreationDate | Format-Table -AutoSize
    Write-Host "本机进程状态：运行中"
}
else {
    Write-Host "本机进程状态：未运行"
}

$MonitorPort = Get-RunnerMonitorPort
try {
    $LocalHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$MonitorPort/healthz" -TimeoutSec 3
    Write-Host "本机接口状态：正常（PID $($LocalHealth.pid)，$($LocalHealth.state)，端口 $MonitorPort）"
}
catch {
    Write-Host "本机接口状态：失败 - $($_.Exception.Message)"
}

try {
    $Health = Invoke-RestMethod -Uri "https://auto.yangtaoer.com.cn/healthz" -TimeoutSec 10
    Write-Host "云端连接状态：正常 ($($Health.mode))"
}
catch {
    Write-Host "云端连接状态：失败 - $($_.Exception.Message)"
}

$LogPath = Join-Path $ProjectRoot "data\runner\logs\runner.log"
if (Test-Path -LiteralPath $LogPath) {
    Write-Host "最近日志："
    Get-Content -LiteralPath $LogPath -Tail 10
}
