$ErrorActionPreference = "Stop"
$RunnerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ClientScript = Join-Path $RunnerDir "client.ps1"
$Shell = New-Object -ComObject WScript.Shell
$Desktop = $Shell.SpecialFolders.Item("Desktop")
$ShortcutPath = Join-Path $Desktop "AutoDev 执行器控制台.lnk"
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = "powershell.exe"
$Shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ClientScript`""
$Shortcut.WorkingDirectory = Split-Path -Parent $RunnerDir
$IconPath = Join-Path (Split-Path -Parent $RunnerDir) "app\static\brand\favicon.ico"
if (Test-Path -LiteralPath $IconPath) {
    $Shortcut.IconLocation = "$IconPath,0"
}
$Shortcut.Description = "查看并控制 AutoDev 本机执行器"
$Shortcut.Save()
Write-Host "已创建桌面快捷方式：$ShortcutPath"
