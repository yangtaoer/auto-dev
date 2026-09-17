param([string]$CacheRoot = (Join-Path $PSScriptRoot '../../data/runner/build-tools'))
$ErrorActionPreference = 'Stop'
# Pinned independently of the machine-wide nvm selection. Official Node.js SHA256.
$Version = '22.23.2'
$ExpectedHash = '1177b4137ba5adaa56354ae40f1080c7450e8ae09cecb47da459d1c52ac99f97'
$CacheRoot = [IO.Path]::GetFullPath($CacheRoot)
$Runtime = Join-Path $CacheRoot "node-v$Version-win-x64"
$Node = Join-Path $Runtime 'node.exe'
if (-not (Test-Path -LiteralPath $Node -PathType Leaf)) {
    New-Item -ItemType Directory -Path $CacheRoot -Force | Out-Null
    $Stage = Join-Path $CacheRoot ('download-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $Stage | Out-Null
    $Archive = Join-Path $Stage 'node.zip'
    Write-Host "准备固定前端运行时 Node v$Version（不修改系统 Node）"
    Invoke-WebRequest "https://nodejs.org/dist/v$Version/node-v$Version-win-x64.zip" -OutFile $Archive -TimeoutSec 300
    if ((Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedHash) {
        throw 'Node 运行时 SHA256 校验失败，拒绝执行'
    }
    Expand-Archive -LiteralPath $Archive -DestinationPath $Stage
    try {
        if (-not (Test-Path -LiteralPath $Runtime)) {
            [IO.Directory]::Move((Join-Path $Stage "node-v$Version-win-x64"), $Runtime)
        }
    }
    catch { if (-not (Test-Path -LiteralPath $Node -PathType Leaf)) { throw } }
    # Only the unique download directory created above may be removed.
    if ([IO.Path]::GetFullPath($Stage).StartsWith($CacheRoot + [IO.Path]::DirectorySeparatorChar)) {
        Remove-Item -LiteralPath $Stage -Recurse -Force
    }
}
$Actual = (& $Node --version).Trim()
if ($LASTEXITCODE -ne 0 -or $Actual -ne "v$Version") { throw "前端运行时版本不匹配：$Actual" }
if (-not (Test-Path -LiteralPath (Join-Path $Runtime 'node_modules/npm/bin/npm-cli.js'))) { throw '固定运行时缺少 npm' }
Write-Output $Runtime
