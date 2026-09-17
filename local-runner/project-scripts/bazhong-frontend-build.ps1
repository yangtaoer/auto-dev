param(
    [Parameter(Mandatory)][string]$FrontendRoot,
    [string]$CacheRoot = (Join-Path $PSScriptRoot '../../data/runner/build-dependencies/bazhong'),
    [string]$NodeDirectory = '',
    [string]$NpmRunner = ''
)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Text.UTF8Encoding]::new($false)
if ($PSStyle) { $PSStyle.OutputRendering = 'PlainText' }
$FrontendRoot = [IO.Path]::GetFullPath($FrontendRoot)
$CacheRoot = [IO.Path]::GetFullPath($CacheRoot)
if (-not (Test-Path -LiteralPath (Join-Path $FrontendRoot 'package.json'))) { throw '缺少前端 package.json' }
if (-not $NodeDirectory) { $NodeDirectory = & (Join-Path $PSScriptRoot 'frontend-node-runtime.ps1') }
$Node = Join-Path $NodeDirectory 'node.exe'
$NpmCli = Join-Path $NodeDirectory 'node_modules/npm/bin/npm-cli.js'
$env:PATH = "$NodeDirectory;$env:PATH"
$env:NO_COLOR = '1'
$env:FORCE_COLOR = '0'
$env:npm_config_progress = 'false'
# Keep npm cache isolated from interactive development installs, but reuse tarballs.
$env:npm_config_cache = Join-Path $CacheRoot 'npm-cache'
New-Item -ItemType Directory -Path $CacheRoot -Force | Out-Null

function Invoke-BuildNpm([string[]]$NpmArguments, [string]$LogFile) {
    if ($NpmRunner) { & $NpmRunner @NpmArguments *> $LogFile }
    else { & $Node $NpmCli @NpmArguments *> $LogFile }
    $Code = $LASTEXITCODE
    Get-Content -LiteralPath $LogFile -Tail 25 | ForEach-Object { Write-Host $_ }
    return $Code
}

# A clean staging tree avoids npm repairing the development session's partial
# node_modules/.package-lock.json. Source files and original dependencies are untouched.
for ($Attempt = 1; $Attempt -le 2; $Attempt++) {
    $Stage = Join-Path $CacheRoot ('build-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $Stage | Out-Null
    foreach ($Item in Get-ChildItem -LiteralPath $FrontendRoot -Force) {
        if ($Item.Name -in @('node_modules', 'dist', '.git', 'release')) { continue }
        if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            ($Item.PSIsContainer -and (Get-ChildItem -LiteralPath $Item.FullName -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }))) {
            throw "前端源码包含链接，拒绝复制到隔离构建目录：$($Item.Name)"
        }
        Copy-Item -LiteralPath $Item.FullName -Destination $Stage -Recurse -Force
    }
    Write-Host "前端隔离构建：$Stage（尝试 $Attempt/2）"
    Push-Location -LiteralPath $Stage
    try {
        $Install = if (Test-Path -LiteralPath (Join-Path $Stage 'package-lock.json')) { 'ci' } else { 'install' }
        $InstallLog = Join-Path $Stage 'install.log'
        $Code = Invoke-BuildNpm @($Install, '--no-audit', '--no-fund', '--prefer-offline', '--fetch-retries=2') $InstallLog
        if ($Code -ne 0) {
            $Log = Get-Content -LiteralPath $InstallLog -Raw
            $Transient = $Code -lt 0 -or $Log -match 'ECONNRESET|ETIMEDOUT|EAI_AGAIN|ECONNREFUSED|EINTEGRITY'
            if ($Attempt -lt 2 -and $Transient) {
                Write-Host "依赖安装瞬时异常（退出码 $Code），保留日志并在新目录重试一次"
                continue
            }
            throw "前端依赖安装失败，退出码 $Code；完整日志：$InstallLog"
        }
        $BuildLog = Join-Path $Stage 'build.log'
        $Code = Invoke-BuildNpm @('run', 'build') $BuildLog
        if ($Code -ne 0) { throw "前端 production 构建失败，退出码 $Code；完整日志：$BuildLog" }
        $Dist = Join-Path $Stage 'dist'
        if (-not (Test-Path -LiteralPath (Join-Path $Dist 'index.html'))) { throw "前端构建未生成 dist/index.html：$BuildLog" }
        $Target = Join-Path $FrontendRoot 'dist'
        if (Test-Path -LiteralPath $Target) {
            $ResolvedTarget = (Resolve-Path -LiteralPath $Target).Path
            if ([IO.Path]::GetFullPath($ResolvedTarget) -ne [IO.Path]::GetFullPath($Target) -or
                ((Get-Item -LiteralPath $Target).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw '拒绝移动链接形式的 dist' }
            # Recoverable backup instead of deleting the previous output.
            Move-Item -LiteralPath $Target -Destination (Join-Path $Stage 'previous-dist')
        }
        Copy-Item -LiteralPath $Dist -Destination $Target -Recurse
        Write-Host "前端构建完成，日志和依赖锁文件保存在：$Stage"
        return
    }
    finally {
        Pop-Location
        $Modules = Join-Path $Stage 'node_modules'
        if ([IO.Path]::GetFullPath($Modules).StartsWith($CacheRoot + [IO.Path]::DirectorySeparatorChar) -and
            (Test-Path -LiteralPath $Modules)) {
            try { Remove-Item -LiteralPath $Modules -Recurse -Force }
            catch { Write-Warning "临时依赖目录暂时被占用，已保留现场：$Modules" }
        }
    }
}
