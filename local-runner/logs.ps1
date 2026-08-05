param(
    [int]$Tail = 100,
    [switch]$Follow
)

$ErrorActionPreference = "Stop"
$RunnerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $RunnerDir
$LogPath = Join-Path $ProjectRoot "data\runner\logs\runner.log"

if (-not (Test-Path -LiteralPath $LogPath)) {
    Write-Host "日志尚未生成：$LogPath"
    Write-Host "请先启动本机执行器。"
    exit 1
}

if ($Follow) {
    Get-Content -LiteralPath $LogPath -Tail $Tail -Wait
}
else {
    Get-Content -LiteralPath $LogPath -Tail $Tail
}
