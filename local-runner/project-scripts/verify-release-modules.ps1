param(
    [string]$PackageDirectory = (Join-Path $env:BUILD_SOURCESDIRECTORY 'target'),
    [string]$ExpectedModules = $env:AUTODEV_EXPECTED_MODULES
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $ExpectedModules) { throw '缺少依赖模块校验信息，请通过 AutoDev 的完整发版流程运行。' }
$Raw = [Convert]::FromBase64String($ExpectedModules)
$Manifest = [Text.Encoding]::UTF8.GetString($Raw) | ConvertFrom-Json
if ($Manifest.version -ne 1) { throw '不支持的依赖模块校验版本' }
$Properties = @($Manifest.modules.PSObject.Properties)
if ($Properties.Count -eq 0 -or $Properties.Count -gt 40) { throw '依赖模块校验清单为空或超过限制' }
$Resolved = (Resolve-Path -LiteralPath $PackageDirectory).Path
$Packages = @(Get-ChildItem -LiteralPath $Resolved -File -Filter 'dcsd-springboot-*.jar')
if ($Packages.Count -ne 1) { throw '构建目录必须包含唯一的业务主包' }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$Archive = [IO.Compression.ZipFile]::OpenRead($Packages[0].FullName)
try {
    foreach ($Property in $Properties) {
        $Name = $Property.Name
        $Expected = [string]$Property.Value.sha256
        if ($Name -notmatch '^dcsd-[a-zA-Z0-9_-]+$' -or $Expected -notmatch '^[a-f0-9]{64}$') { throw '非法的依赖模块校验项' }
        $Entries = @($Archive.Entries | Where-Object { $_.FullName.StartsWith("BOOT-INF/lib/$Name-") -and $_.FullName.EndsWith('.jar') })
        if ($Entries.Count -ne 1) { throw "主包中找不到唯一的依赖模块：$Name" }
        $Stream = $Entries[0].Open()
        $Hasher = [Security.Cryptography.SHA256]::Create()
        try { $Actual = ([BitConverter]::ToString($Hasher.ComputeHash($Stream))).Replace('-', '').ToLowerInvariant() }
        finally { $Hasher.Dispose(); $Stream.Dispose() }
        if ($Actual -ne $Expected) { throw "主包引用了错误或过期的依赖模块：$Name；期望 $Expected，实际 $Actual。停止发布。" }
        Write-Output "MODULE_VERIFIED $Name $Actual"
    }
}
finally { $Archive.Dispose() }
$Hasher = [Security.Cryptography.SHA256]::Create()
try { $Digest = ([BitConverter]::ToString($Hasher.ComputeHash($Raw))).Replace('-', '').ToLowerInvariant() }
finally { $Hasher.Dispose() }
Write-Output "AUTODEV_MODULES_VERIFIED:$Digest"
