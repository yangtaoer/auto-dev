$ErrorActionPreference = "Stop"
$RunnerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $RunnerDir))
$TaskName = "AutoDevLocalRunner"
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

$Processes = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and
    $_.CommandLine.Contains("app.local_runner_main") -and
    $_.CommandLine.Contains($ProjectRoot)
}
if ($Processes) {
    $Processes | Select-Object ProcessId, Name, CreationDate | Format-Table -AutoSize
    Write-Host "本机进程状态：运行中"
}
else {
    Write-Host "本机进程状态：未运行"
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
