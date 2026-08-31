param(
    [Parameter(Mandatory)][string]$FrontendRoot,
    [Parameter(Mandatory)][string]$SourceRoot,
    [Parameter(Mandatory)][string]$Npm,
    [string]$CacheRoot,
    [switch]$PrepareOnly
)

$ErrorActionPreference = "Stop"
$FrontendRoot = [IO.Path]::GetFullPath($FrontendRoot)
$ManifestPath = Join-Path $FrontendRoot "package.json"
$ManifestBytes = [IO.File]::ReadAllBytes($ManifestPath)
$Manifest = [Text.Encoding]::UTF8.GetString($ManifestBytes).TrimStart([char]0xFEFF) | ConvertFrom-Json
$OfflineDependencies = @("wechart_client", "thpush-lib") | Where-Object { $Manifest.dependencies.PSObject.Properties[$_] }

# This dependency is no longer published in npm. Package the verified local copy,
# not the entire mutable node_modules tree. Keep the archive by content hash for audit/replay.
$PlatformRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
if (-not $CacheRoot) { $CacheRoot = Join-Path $PlatformRoot "data\runner\build-dependencies" }
$CacheRoot = [IO.Path]::GetFullPath($CacheRoot)
New-Item -ItemType Directory -Path $CacheRoot -Force | Out-Null
$TemporaryRoot = Join-Path $CacheRoot ("prepare-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $TemporaryRoot | Out-Null
$LockPath = Join-Path $FrontendRoot "package-lock.json"
$HadLock = Test-Path -LiteralPath $LockPath -PathType Leaf
$LockBytes = if ($HadLock) { [IO.File]::ReadAllBytes($LockPath) } else { $null }
$ManifestChanged = $false
try {
    $Archives = @{}
    $Evidence = @()
    foreach ($Name in $OfflineDependencies) {
        $ExpectedVersion = [string]$Manifest.dependencies.$Name
        $Dependency = Join-Path ([IO.Path]::GetFullPath($SourceRoot)) "node_modules\$Name"
        $DependencyManifest = Join-Path $Dependency "package.json"
        if (-not (Test-Path -LiteralPath $DependencyManifest -PathType Leaf)) {
            throw "缺少本机已验证的 $Name 依赖。请在 APP 主仓库恢复后重试；不会从失效的 npm 源下载。"
        }
        $Installed = Get-Content -LiteralPath $DependencyManifest -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($Installed.name -ne $Name -or $Installed.version -ne $ExpectedVersion) {
            throw "本机 $Name 版本与隔离仓库不一致：需要 $ExpectedVersion，实际 $($Installed.version)"
        }
        $PackageDirectory = Join-Path $TemporaryRoot $Name
        New-Item -ItemType Directory -Path $PackageDirectory | Out-Null
        Push-Location -LiteralPath $PackageDirectory
        try {
            # Packing in the temporary cwd also supports the machine's legacy npm 6.
            $PackOutput = & $Npm pack $Dependency --ignore-scripts --json
            if ($LASTEXITCODE -ne 0) { throw "本地 $Name 离线依赖封装失败" }
        }
        finally { Pop-Location }
        $Archive = Get-ChildItem -LiteralPath $PackageDirectory -File -Filter "*.tgz"
        if (@($Archive).Count -ne 1) { throw "离线依赖必须生成唯一的 tgz 文件" }
        $Hash = (Get-FileHash -LiteralPath $Archive.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        $CachedArchive = Join-Path $CacheRoot "$Hash.tgz"
        if (-not (Test-Path -LiteralPath $CachedArchive)) {
            Copy-Item -LiteralPath $Archive.FullName -Destination $CachedArchive
        }
        if ((Get-FileHash -LiteralPath $CachedArchive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Hash) {
            throw "离线依赖缓存校验失败"
        }
        Write-Host "$Name $ExpectedVersion 离线依赖 SHA256=$Hash"
        $Evidence += "$Name@$ExpectedVersion=$Hash"
        $Archives[$Name] = $CachedArchive
    }
    if ($PrepareOnly) { $Archives.Values | Write-Output; return }

    foreach ($Name in $Archives.Keys) { $Manifest.dependencies.$Name = "file:" + $Archives[$Name].Replace('\', '/') }
    $ManifestChanged = $true
    [IO.File]::WriteAllText($ManifestPath, ($Manifest | ConvertTo-Json -Depth 50), [Text.UTF8Encoding]::new($false))
    Push-Location -LiteralPath $FrontendRoot
    try {
        # npm install also reconciles an existing lock with the local archive. Restore
        # both manifests afterwards so build-only dependency plumbing never enters a PR.
        & $Npm install --no-audit --no-fund --prefer-offline --legacy-peer-deps
        if ($LASTEXITCODE -ne 0) { throw "APP 前端依赖安装失败" }
        & $Npm run nanchongydzt-build -- --skip-plugins @vue/cli-plugin-eslint
        if ($LASTEXITCODE -ne 0) { throw "APP 前端构建失败" }
        $env:AUTODEV_APP_DEPENDENCY_HASHES = $Evidence -join '; '
        Write-Host "APP 构建依赖：$env:AUTODEV_APP_DEPENDENCY_HASHES"
    }
    finally { Pop-Location }
}
finally {
    if ($ManifestChanged) {
        [IO.File]::WriteAllBytes($ManifestPath, $ManifestBytes)
        if ($HadLock) { [IO.File]::WriteAllBytes($LockPath, $LockBytes) }
        elseif (Test-Path -LiteralPath $LockPath -PathType Leaf) { Remove-Item -LiteralPath $LockPath }
    }
    $ResolvedTemporaryRoot = [IO.Path]::GetFullPath($TemporaryRoot)
    if (-not $ResolvedTemporaryRoot.StartsWith([IO.Path]::GetFullPath($CacheRoot) + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理依赖缓存目录之外的路径"
    }
    Remove-Item -LiteralPath $ResolvedTemporaryRoot -Recurse -Force
}
