param(
    [string]$RepositoryRoot = (Get-Location).Path,
    [string]$BaseBranch = "dev"
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = [System.IO.Path]::GetFullPath($RepositoryRoot)
$GitDir = Join-Path $RepositoryRoot ".git"
if (-not (Test-Path -LiteralPath $GitDir)) {
    throw "当前目录不是巴中项目 Git 工作区：$RepositoryRoot"
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

# Vite 6 不支持本机默认的 Node 14。优先从 nvm-windows 中选择已安装的最高版本。
$NodeDirectory = $null
if ($env:NVM_HOME -and (Test-Path -LiteralPath $env:NVM_HOME)) {
    $NodeDirectory = Get-ChildItem -LiteralPath $env:NVM_HOME -Directory -Filter "v*" |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "node.exe") } |
        Sort-Object { try { [version]$_.Name.TrimStart("v") } catch { [version]"0.0" } } -Descending |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $NodeDirectory) {
    $NodeCommand = Get-Command node.exe -ErrorAction Stop
    $NodeDirectory = Split-Path -Parent $NodeCommand.Source
}
$env:PATH = "$NodeDirectory;$env:PATH"
$NodeMajor = [int]((& (Join-Path $NodeDirectory "node.exe") --version).TrimStart("v").Split(".")[0])
if ($NodeMajor -lt 18) {
    throw "前端构建需要 Node 18+，当前检测到 Node $NodeMajor"
}

$Maven = (Get-Command mvn.cmd -ErrorAction Stop).Source
$Npm = Join-Path $NodeDirectory "npm.cmd"
if (-not (Test-Path -LiteralPath $Npm)) {
    throw "所选 Node 目录中缺少 npm.cmd：$NodeDirectory"
}

Write-Host "[1/4] 构建后端"
Invoke-Checked -FilePath $Maven -Arguments @("clean", "package", "-DskipTests") -WorkingDirectory $RepositoryRoot

$FrontendRoot = Join-Path $RepositoryRoot "th-dc-biz-bazhong-vue"
Write-Host "[2/4] 安装前端依赖并构建（Node $(& (Join-Path $NodeDirectory 'node.exe') --version)）"
Push-Location -LiteralPath $FrontendRoot
try {
    if (Test-Path -LiteralPath (Join-Path $FrontendRoot "package-lock.json") -PathType Leaf) {
        Invoke-Checked -FilePath $Npm -Arguments @("ci", "--no-audit", "--no-fund") -WorkingDirectory $FrontendRoot
    }
    else {
        Invoke-Checked -FilePath $Npm -Arguments @("install", "--no-audit", "--no-fund") -WorkingDirectory $FrontendRoot
    }
    Invoke-Checked -FilePath $Npm -Arguments @("run", "build") -WorkingDirectory $FrontendRoot
}
finally {
    Pop-Location
}

$JarSource = Join-Path $RepositoryRoot "target\th-dc-biz-bazhong-1.0.0-SNAPSHOT.jar"
$FrontendDist = Join-Path $FrontendRoot "dist"
if (-not (Test-Path -LiteralPath $JarSource -PathType Leaf)) { throw "后端 JAR 未生成：$JarSource" }
if (-not (Test-Path -LiteralPath $FrontendDist -PathType Container)) { throw "前端 dist 未生成：$FrontendDist" }

$Branch = (& git -C $RepositoryRoot branch --show-current).Trim()
$WorkItemId = if ($Branch -match "feature/(\d+)-") { $Matches[1] } else { "unknown" }
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

$ChangedPaths = & git -C $RepositoryRoot diff --name-only "origin/$BaseBranch...HEAD" --
foreach ($RelativePath in $ChangedPaths) {
    $Normalized = $RelativePath.Replace("/", "\")
    if ($RelativePath -match "\.sql$") {
        $SqlSource = Join-Path $RepositoryRoot $Normalized
        if (Test-Path -LiteralPath $SqlSource -PathType Leaf) {
            Copy-Item -LiteralPath $SqlSource -Destination (Join-Path $ReleaseDir "sql")
        }
    }
}
if (-not (Get-ChildItem -LiteralPath (Join-Path $ReleaseDir "sql") -File -ErrorAction SilentlyContinue)) {
    Remove-Item -LiteralPath (Join-Path $ReleaseDir "sql") -Force
}

$ReleaseNotes = @(
    "# 巴中自巡航交付包"
    ""
    "- TFS 需求：#$WorkItemId"
    "- 来源分支：$Branch"
    "- 基础分支：$BaseBranch"
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
