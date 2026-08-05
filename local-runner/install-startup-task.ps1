$ErrorActionPreference = "Stop"
$RunnerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
& (Join-Path $RunnerDir "install.ps1") -RegisterStartupTask
Start-ScheduledTask -TaskName "AutoDevLocalRunner"
Write-Host "AutoDevLocalRunner 已启动。"
