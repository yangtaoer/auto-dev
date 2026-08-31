param(
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$WorkspaceRoot = [System.IO.Path]::GetFullPath((Get-Location).Path)
$FrontendRoot = Join-Path $WorkspaceRoot "dcsd-app-ui"
$BackendRoot = Join-Path $WorkspaceRoot "dcsd-app-starter"

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

function Assert-Contains {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Pattern,
        [Parameter(Mandatory)][string]$Message
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "缺少文件：$Path"
    }
    $Content = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    if ($Content -notmatch $Pattern) {
        throw $Message
    }
}

$ChangedRepositories = @()
if ($env:AUTODEV_CHANGED_REPOSITORIES) {
    try {
        $ChangedRepositories = @($env:AUTODEV_CHANGED_REPOSITORIES | ConvertFrom-Json)
    }
    catch {
        throw "AUTODEV_CHANGED_REPOSITORIES 不是有效 JSON：$($_.Exception.Message)"
    }
}
$ChangedRepositories = @($ChangedRepositories | ForEach-Object { [string]$_ } | Where-Object { $_ })
if ($ChangedRepositories.Count -eq 0) {
    throw "未收到本次变更仓库列表，拒绝构建未修改的前端或后端"
}
$UnknownRepositories = @($ChangedRepositories | Where-Object { $_ -notin @("dcsd-app-ui", "dcsd-app-starter") })
if ($UnknownRepositories.Count -gt 0) {
    throw "存在未识别的 APP 仓库：$($UnknownRepositories -join '、')"
}
$BuildFrontend = $ChangedRepositories -contains "dcsd-app-ui"
$BuildBackend = $ChangedRepositories -contains "dcsd-app-starter"

$NodeDirectory = $null
if ($env:NVM_HOME -and (Test-Path -LiteralPath $env:NVM_HOME)) {
    $InstalledNodeDirectories = @(Get-ChildItem -LiteralPath $env:NVM_HOME -Directory -Filter "v*" |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "node.exe") } |
        Sort-Object { try { [version]$_.Name.TrimStart("v") } catch { [version]"0.0" } } -Descending)
    # Vue CLI 4 / webpack 4 is most reliable on Node 16. Prefer that LTS line over a newer Node runtime.
    $NodeDirectory = $InstalledNodeDirectories |
        Where-Object { try { ([version]$_.Name.TrimStart("v")).Major -eq 16 } catch { $false } } |
        Select-Object -First 1 -ExpandProperty FullName
    if (-not $NodeDirectory) {
        $NodeDirectory = $InstalledNodeDirectories | Select-Object -First 1 -ExpandProperty FullName
    }
}
if (-not $NodeDirectory -and $BuildFrontend) {
    $NodeDirectory = Split-Path -Parent (Get-Command node.exe -ErrorAction Stop).Source
}
if ($BuildFrontend) {
    $Node = Join-Path $NodeDirectory "node.exe"
    $Npm = Join-Path $NodeDirectory "npm.cmd"
    if (-not (Test-Path -LiteralPath $Npm -PathType Leaf)) {
        throw "所选 Node 目录中缺少 npm.cmd：$NodeDirectory"
    }
    $env:PATH = "$NodeDirectory;$env:PATH"
    $NodeMajor = [int]((& $Node --version).TrimStart("v").Split(".")[0])
    if ($NodeMajor -ge 17) {
        $env:NODE_OPTIONS = (($env:NODE_OPTIONS, "--openssl-legacy-provider") -join " ").Trim()
    }
}

$Maven = $null
if ($BuildBackend) {
    $MavenCommand = Get-Command mvn.cmd -ErrorAction SilentlyContinue
    if ($MavenCommand) {
        $Maven = $MavenCommand.Source
    }
    else {
        $Maven = "C:\tool\apache-maven-3.9.16\bin\mvn.cmd"
        if (-not (Test-Path -LiteralPath $Maven -PathType Leaf)) {
            throw "未找到 Maven，请安装 mvn.cmd 或配置 C:\tool\apache-maven-3.9.16"
        }
    }
}

if ($BuildFrontend) {
    if (-not (Test-Path -LiteralPath (Join-Path $FrontendRoot ".git"))) {
        throw "缺少前端隔离仓库：$FrontendRoot"
    }
    $EnvironmentFile = Join-Path $FrontendRoot ".env.nanchongydzt"
    Assert-Contains $EnvironmentFile '(?m)^VUE_APP_AREA_DIR\s*=\s*[''"]@/views/nanchong[''"]' "南充移动中台配置缺少 nanchong 页面目录"
    Assert-Contains $EnvironmentFile '(?m)^VUE_APP_PLATFORM\s*=\s*[''"]?7[''"]?' "南充移动中台配置的平台必须为 7"
    Assert-Contains $EnvironmentFile '(?m)^VUE_APP_YDZT_BASE_URL\s*=\s*[''"]https://mamsc\.sgcc\.com\.cn/isc/router[''"]' "南充移动中台 URL 配置不正确"
    Assert-Contains $EnvironmentFile '(?m)^VUE_APP_YDZT_APP_KEY\s*=\s*[''"][^''"]+[''"]' "南充移动中台 appKey 未配置"
    Assert-Contains (Join-Path $FrontendRoot "src\util\status.js") "isYDZT[\s\S]*VUE_APP_PLATFORM[\s\S]*ydztConfig" "前端缺少移动中台平台识别与 appKey 配置逻辑"
    Assert-Contains (Join-Path $FrontendRoot "src\views\nanchong\mixin\login.js") "fetchYdztToken[\s\S]*manualLoginMode[\s\S]*login" "前端缺少移动中台免密登录与失败回退逻辑"
    Assert-Contains (Join-Path $FrontendRoot "src\plugins\http-client.js") "ydztConfig\.appKey[\s\S]*appaccess" "前端缺少移动中台 appKey 网关请求逻辑"
    Write-Host "构建 APP 前端：nanchongydzt / platform 7"
    if (-not $env:AUTODEV_APP_FRONTEND_SOURCE) {
        throw "缺少 AUTODEV_APP_FRONTEND_SOURCE，无法取得已验证的移动中台离线依赖"
    }
    # nanchongydzt-build uses the same verified local dependency as the APP conversation.
    & (Join-Path $PSScriptRoot "app-frontend-build.ps1") -FrontendRoot $FrontendRoot -SourceRoot $env:AUTODEV_APP_FRONTEND_SOURCE -Npm $Npm
    if (-not (Test-Path -LiteralPath (Join-Path $FrontendRoot "dist") -PathType Container)) {
        throw "APP 前端 dist 未生成"
    }
}

if ($BuildBackend) {
    if (-not (Test-Path -LiteralPath (Join-Path $BackendRoot "pom.xml") -PathType Leaf)) {
        throw "缺少后端隔离仓库或 pom.xml：$BackendRoot"
    }
    Write-Host "构建 APP 后端"
    Invoke-Checked -FilePath $Maven -Arguments @("clean", "package", "-DskipTests") -WorkingDirectory $BackendRoot
    $BackendJar = Get-ChildItem -LiteralPath (Join-Path $BackendRoot "target") -Filter "*.jar" -File |
        Where-Object { $_.Name -notmatch "^(original-|.*-(sources|javadoc)\.jar$)" } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $BackendJar) {
        throw "APP 后端 JAR 未生成"
    }
}

if ($ValidateOnly) {
    Write-Host "PR 前校验完成，仅验证本次变更端：$($ChangedRepositories -join '、')"
    if ($BuildFrontend) { Write-Host "APP 构建依赖：$env:AUTODEV_APP_DEPENDENCY_HASHES" }
    return
}

$ReleaseRoot = [System.IO.Path]::GetFullPath((Join-Path $WorkspaceRoot "release"))
New-Item -ItemType Directory -Force -Path $ReleaseRoot | Out-Null
$WorkItemId = if ($env:AUTODEV_WORK_ITEM_ID) { $env:AUTODEV_WORK_ITEM_ID } else { "unknown" }
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"

if ($BuildFrontend) {
    $StagingRoot = [System.IO.Path]::GetFullPath((Join-Path $ReleaseRoot ".app-ui-$WorkItemId-$Timestamp"))
    if (-not $StagingRoot.StartsWith($ReleaseRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝在 release 目录之外组装前端交付物"
    }
    $PackageRoot = Join-Path $StagingRoot "ddyxzhyy"
    New-Item -ItemType Directory -Force -Path $PackageRoot | Out-Null
    Copy-Item -Path (Join-Path $FrontendRoot "dist\*") -Destination $PackageRoot -Recurse -Force
    $FrontendZip = Join-Path $ReleaseRoot "ddyxzhyy.zip"
    Compress-Archive -Path (Join-Path $StagingRoot "*") -DestinationPath $FrontendZip -CompressionLevel Optimal -Force
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $Archive = [System.IO.Compression.ZipFile]::OpenRead($FrontendZip)
    try {
        $InvalidEntries = @($Archive.Entries | Where-Object { $_.FullName -and -not $_.FullName.StartsWith("ddyxzhyy/", [System.StringComparison]::OrdinalIgnoreCase) })
        if ($InvalidEntries.Count -gt 0) {
            throw "APP 前端 ZIP 根目录不是 ddyxzhyy/：$($InvalidEntries[0].FullName)"
        }
    }
    finally {
        $Archive.Dispose()
    }
    Remove-Item -LiteralPath $StagingRoot -Recurse -Force
    Write-Output $FrontendZip
}

if ($BuildBackend) {
    $DeliveredJar = Join-Path $ReleaseRoot $BackendJar.Name
    Copy-Item -LiteralPath $BackendJar.FullName -Destination $DeliveredJar -Force
    Write-Output $DeliveredJar
}

Write-Host "APP 按变更端打包完成：$($ChangedRepositories -join '、')"
if ($BuildFrontend) { Write-Host "APP 构建依赖：$env:AUTODEV_APP_DEPENDENCY_HASHES" }
