$ErrorActionPreference = "Stop"
$RunnerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $RunnerDir))
$TaskName = "AutoDevLocalRunner"
$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if ($Task -and $Task.State -eq "Running") {
    Stop-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 2
}

$Processes = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and
    $_.CommandLine.Contains("app.local_runner_main") -and
    $_.CommandLine.Contains($ProjectRoot)
}
foreach ($Process in $Processes) {
    Stop-Process -Id $Process.ProcessId -Force
}

Write-Host "本机执行器已停止。"
