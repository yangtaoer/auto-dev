$ErrorActionPreference = "Stop"
$RunnerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$TaskName = "AutoDevLocalRunner"
& (Join-Path $RunnerDir "stop.ps1")

$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Task) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "本机执行器已通过计划任务重新启动。"
}
else {
    $StartScript = Join-Path $RunnerDir "start.ps1"
    Start-Process -FilePath "powershell.exe" -ArgumentList "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$StartScript`"" -WorkingDirectory (Split-Path -Parent $RunnerDir) -WindowStyle Hidden
    Write-Host "本机执行器已在后台重新启动。"
}
Start-Sleep -Seconds 3
& (Join-Path $RunnerDir "status.ps1")
