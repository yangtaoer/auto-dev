$ErrorActionPreference = "Stop"

$WorkspaceRoot = (Get-Location).Path
$MavenCommand = Get-Command mvn.cmd -ErrorAction SilentlyContinue
if ($MavenCommand) {
    $Maven = $MavenCommand.Source
}
else {
    $Maven = "C:\tool\apache-maven-3.9.16\bin\mvn.cmd"
    if (-not (Test-Path -LiteralPath $Maven -PathType Leaf)) {
        throw "未找到 Maven，请安装 mvn.cmd 或更新项目构建脚本中的 Maven 路径"
    }
}

$ChangedRepositories = @()
Get-ChildItem -LiteralPath $WorkspaceRoot -Directory | ForEach-Object {
    $Repository = $_.FullName
    if (Test-Path -LiteralPath (Join-Path $Repository ".git")) {
        & git -C $Repository diff --quiet "origin/dev...HEAD" --
        $DiffExitCode = $LASTEXITCODE
        if ($DiffExitCode -eq 1) {
            $ChangedRepositories += $_
        }
        elseif ($DiffExitCode -ne 0) {
            throw "无法检查仓库 $($_.Name) 的 dev 分支差异，git 退出码：$DiffExitCode"
        }
    }
}

if ($ChangedRepositories.Count -eq 0) {
    throw "未找到发生代码变更的仓库，无法执行 PR 前构建校验"
}

foreach ($Repository in $ChangedRepositories) {
    $Pom = Join-Path $Repository.FullName "pom.xml"
    if (-not (Test-Path -LiteralPath $Pom -PathType Leaf)) {
        throw "仓库 $($Repository.Name) 缺少 pom.xml，无法执行标准 Maven 构建"
    }
    Write-Host "正在构建变更仓库：$($Repository.Name)"
    & $Maven -f $Pom -DskipTests package
    if ($LASTEXITCODE -ne 0) {
        throw "仓库 $($Repository.Name) 构建失败，Maven 退出码：$LASTEXITCODE"
    }
}

Write-Host "PR 前构建校验完成，共验证 $($ChangedRepositories.Count) 个仓库"
