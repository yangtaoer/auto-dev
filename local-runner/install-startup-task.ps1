param([switch]$Elevated)

$ErrorActionPreference = "Stop"
$RunnerDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$Identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$Principal = New-Object System.Security.Principal.WindowsPrincipal($Identity)
$IsAdministrator = $Principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $IsAdministrator) {
    $PowerShell = (Get-Process -Id $PID).Path
    $Arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`"",
        "-Elevated"
    )
    Write-Host "注册开机自启需要管理员权限，正在请求 Windows 授权..."
    $Process = Start-Process -FilePath $PowerShell -ArgumentList $Arguments -Verb RunAs -Wait -PassThru
    if ($Process.ExitCode -ne 0) {
        throw "开机自启安装失败，管理员进程退出码：$($Process.ExitCode)"
    }
    Write-Host "开机自启安装完成。"
    exit 0
}

& (Join-Path $RunnerDir "install.ps1") -RegisterStartupTask
& (Join-Path $RunnerDir "restart.ps1")
