$ErrorActionPreference = "Stop"
$RunnerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $RunnerDir
$RunnerPythonw = Join-Path $ProjectRoot ".venv-runner\Scripts\pythonw.exe"
$SharedPythonw = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
if (Test-Path -LiteralPath $RunnerPythonw) {
    $Pythonw = $RunnerPythonw
}
elseif (Test-Path -LiteralPath $SharedPythonw) {
    $Pythonw = $SharedPythonw
}
else {
    throw "缺少本机执行器 Python 环境，请先运行 local-runner\install.ps1。"
}
$Shell = New-Object -ComObject WScript.Shell
$Desktop = $Shell.SpecialFolders.Item("Desktop")
$ShortcutPath = Join-Path $Desktop "AutoDev 执行器控制台.lnk"
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $Pythonw
$Shortcut.Arguments = "-m app.local_client"
$Shortcut.WorkingDirectory = $ProjectRoot
$IconPath = Join-Path $ProjectRoot "app\static\brand\favicon.ico"
if (Test-Path -LiteralPath $IconPath) {
    $Shortcut.IconLocation = "$IconPath,0"
}
$Shortcut.Description = "查看并控制 AutoDev 本机执行器"
$Shortcut.Save()
$Verified = $Shell.CreateShortcut($ShortcutPath)
if (-not $Verified.TargetPath) {
    throw "快捷方式创建后校验失败：$ShortcutPath"
}
foreach ($LegacyName in @("AutoDev Runner Console.lnk", "CodeShip 执行器控制台.lnk", "既济执行器控制台.lnk")) {
    $LegacyShortcutPath = Join-Path $Desktop $LegacyName
    if ((Test-Path -LiteralPath $LegacyShortcutPath) -and $LegacyShortcutPath -ne $ShortcutPath) {
        Remove-Item -LiteralPath $LegacyShortcutPath -Force
    }
}
Write-Host "已创建并校验桌面快捷方式：$ShortcutPath"
