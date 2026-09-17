param(
    [string]$RepositoryRoot = (Get-Location).Path,
    [string]$BaseBranch = "dev"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
if ($PSStyle) { $PSStyle.OutputRendering = 'PlainText' }
$RepositoryRoot = [System.IO.Path]::GetFullPath($RepositoryRoot)
$GitDir = Join-Path $RepositoryRoot ".git"
if (-not (Test-Path -LiteralPath $GitDir)) {
    throw "当前目录不是巴中项目 Git 工作区：$RepositoryRoot"
}

# A delivery package must be built from the commit already on the target branch.
if ($env:AUTODEV_TARGET_BRANCH) { $BaseBranch = $env:AUTODEV_TARGET_BRANCH }
if (-not $env:AUTODEV_BUILD_COMMIT) {
    & git -C $RepositoryRoot fetch origin $BaseBranch
    if ($LASTEXITCODE -ne 0) { throw "无法更新目标分支，未执行打包" }
}
$BuildCommit = (& git -C $RepositoryRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw "无法读取构建提交" }
$RemoteCommit = (& git -C $RepositoryRoot rev-parse "refs/remotes/origin/$BaseBranch").Trim()
if ($LASTEXITCODE -ne 0 -or $BuildCommit -ne $RemoteCommit) {
    throw "当前提交未与 origin/$BaseBranch 一致，必须先合入目标分支再本地打包"
}
if ($env:AUTODEV_BUILD_COMMIT -and $BuildCommit -ne $env:AUTODEV_BUILD_COMMIT) {
    throw "构建提交与平台冻结的已推送提交不一致，未执行打包"
}
Write-Host "构建基线已确认：origin/$BaseBranch @ $BuildCommit"
$TrackedChanges = @(& git -C $RepositoryRoot diff HEAD --name-only)
if ($LASTEXITCODE -ne 0 -or $TrackedChanges.Count -gt 0) {
    throw "存在未提交的已跟踪文件改动，不能混入正式交付包"
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][string[]]$Arguments,
        [Parameter(Mandatory)][string]$WorkingDirectory
    )
    Push-Location -LiteralPath $WorkingDirectory
    try {
        & $FilePath @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$FilePath 执行失败，退出码 $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
}

# Do not select the highest nvm version: use a verified, project-pinned runtime.
$NodeDirectory = & (Join-Path $PSScriptRoot 'frontend-node-runtime.ps1')
$env:PATH = "$NodeDirectory;$env:PATH"

$Maven = (Get-Command mvn.cmd -ErrorAction Stop).Source

Write-Host "[1/4] 构建后端"
Invoke-Checked -FilePath $Maven -Arguments @("clean", "package", "-DskipTests") -WorkingDirectory $RepositoryRoot

$FrontendRoot = Join-Path $RepositoryRoot "th-dc-biz-bazhong-vue"
Write-Host "[2/4] 安装前端依赖并构建（Node $(& (Join-Path $NodeDirectory 'node.exe') --version)）"
& (Join-Path $PSScriptRoot 'bazhong-frontend-build.ps1') -FrontendRoot $FrontendRoot -NodeDirectory $NodeDirectory

$JarSource = Join-Path $RepositoryRoot "target\th-dc-biz-bazhong-1.0.0-SNAPSHOT.jar"
$FrontendDist = Join-Path $FrontendRoot "dist"
if (-not (Test-Path -LiteralPath $JarSource -PathType Leaf)) { throw "后端 JAR 未生成：$JarSource" }
if (-not (Test-Path -LiteralPath $FrontendDist -PathType Container)) { throw "前端 dist 未生成：$FrontendDist" }

$Branch = (& git -C $RepositoryRoot branch --show-current).Trim()
$WorkItemId = "unknown"
if ($env:AUTODEV_WORK_ITEM_ID) { $WorkItemId = $env:AUTODEV_WORK_ITEM_ID }
$FeaturePrefix = "feature/"
if ($Branch.StartsWith($FeaturePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    $CandidateWorkItemId = $Branch.Substring($FeaturePrefix.Length).Split("-")[0]
    $ParsedWorkItemId = 0
    if ([int]::TryParse($CandidateWorkItemId, [ref]$ParsedWorkItemId)) {
        $WorkItemId = $ParsedWorkItemId.ToString()
    }
}
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$ReleaseRoot = Join-Path $RepositoryRoot "release"
$ReleaseName = "bazhong-$WorkItemId-$Timestamp"
$ReleaseDir = Join-Path $ReleaseRoot $ReleaseName
$ZipPath = "$ReleaseDir.zip"

$ResolvedReleaseRoot = [System.IO.Path]::GetFullPath($ReleaseRoot)
$ResolvedReleaseDir = [System.IO.Path]::GetFullPath($ReleaseDir)
if (-not $ResolvedReleaseDir.StartsWith($ResolvedReleaseRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "拒绝在 release 目录之外生成或清理交付物：$ResolvedReleaseDir"
}
New-Item -ItemType Directory -Force -Path (Join-Path $ReleaseDir "sql") | Out-Null

Write-Host "[3/4] 组装交付目录"
Copy-Item -LiteralPath $JarSource -Destination (Join-Path $ReleaseDir "th-dc-biz-bazhong.jar")
Copy-Item -LiteralPath $FrontendDist -Destination (Join-Path $ReleaseDir "dist") -Recurse
foreach ($Name in @("env.sh", "start.sh", "nginx.conf", "README.md")) {
    $Source = Join-Path $RepositoryRoot "deploy\$Name"
    if (Test-Path -LiteralPath $Source -PathType Leaf) {
        $DestinationName = if ($Name -eq "README.md") { "DEPLOYMENT.md" } else { $Name }
        Copy-Item -LiteralPath $Source -Destination (Join-Path $ReleaseDir $DestinationName)
    }
}

$ChangedPaths = if ($env:AUTODEV_CHANGED_FILES) {
    @($env:AUTODEV_CHANGED_FILES | ConvertFrom-Json)
} else {
    @(& git -C $RepositoryRoot diff-tree --no-commit-id --name-only -r HEAD)
}
foreach ($RelativePath in $ChangedPaths) {
    $ResolvedSource = [System.IO.Path]::GetFullPath((Join-Path $RepositoryRoot $RelativePath))
    if (-not $ResolvedSource.StartsWith($RepositoryRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "本次改动清单包含工作区之外的路径"
    }
    $Normalized = $RelativePath.Replace("/", "\")
    if ($RelativePath -match "\.sql$") {
        $SqlSource = Join-Path $RepositoryRoot $Normalized
        if (Test-Path -LiteralPath $SqlSource -PathType Leaf) {
            $SqlDestination = Join-Path (Join-Path $ReleaseDir "sql") $Normalized
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $SqlDestination) | Out-Null
            Copy-Item -LiteralPath $SqlSource -Destination $SqlDestination
        }
    }
}
if (-not (Get-ChildItem -LiteralPath (Join-Path $ReleaseDir "sql") -File -Recurse -ErrorAction SilentlyContinue)) {
    Remove-Item -LiteralPath (Join-Path $ReleaseDir "sql") -Force
}

$ReleaseNotes = @(
    "# 巴中自巡航交付包"
    ""
    "- TFS 需求：#$WorkItemId"
    "- 来源分支：$Branch"
    "- 基础分支：$BaseBranch"
    "- 已推送构建提交：$BuildCommit"
    "- 生成时间：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    "- 后端：th-dc-biz-bazhong.jar"
    "- 前端：dist/"
)
$ReleaseNotes | Set-Content -LiteralPath (Join-Path $ReleaseDir "RELEASE-NOTES.md") -Encoding utf8

$Checksums = Get-ChildItem -LiteralPath $ReleaseDir -File -Recurse |
    Sort-Object FullName |
    ForEach-Object {
        $RelativePath = [System.IO.Path]::GetRelativePath($ReleaseDir, $_.FullName).Replace("\", "/")
        $Hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$Hash  $RelativePath"
    }
$Checksums | Set-Content -LiteralPath (Join-Path $ReleaseDir "SHA256SUMS.txt") -Encoding utf8

Write-Host "[4/4] 压缩交付包"
Compress-Archive -Path (Join-Path $ReleaseDir "*") -DestinationPath $ZipPath -CompressionLevel Optimal
Write-Output $ZipPath
