param(
    [string]$Repository = "yangtaoer/auto-dev"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -LiteralPath $ProjectRoot

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "缺少 GitHub CLI (gh)"
}
$Branch = (& git branch --show-current).Trim()
if ($Branch -ne "main") {
    throw "只允许从 main 发布 latest，当前分支：$Branch"
}
if (& git status --porcelain) {
    throw "工作区存在未提交变更，请先提交并推送 main"
}

& (Join-Path $ProjectRoot "scripts\package.ps1")
if ($LASTEXITCODE -ne 0) { throw "部署包生成失败" }

$Version = (Get-Content -Raw -LiteralPath (Join-Path $ProjectRoot "VERSION")).Trim()
$VersionedZip = Join-Path $ProjectRoot "dist\autodev-hybrid-$Version.zip"
$LatestZip = Join-Path $ProjectRoot "dist\autodev-hybrid-latest.zip"
$ChecksumFile = Join-Path $ProjectRoot "dist\SHA256SUMS.txt"
Copy-Item -LiteralPath $VersionedZip -Destination $LatestZip -Force
$Hash = (Get-FileHash -LiteralPath $LatestZip -Algorithm SHA256).Hash.ToLowerInvariant()
"$Hash  autodev-hybrid-latest.zip" | Set-Content -LiteralPath $ChecksumFile -Encoding utf8NoBOM

$Commit = (& git rev-parse HEAD).Trim()
& git tag -f latest $Commit
if ($LASTEXITCODE -ne 0) { throw "更新本地 latest 标签失败" }
& git push origin refs/tags/latest --force
if ($LASTEXITCODE -ne 0) { throw "推送 latest 标签失败" }

& gh release view latest --repo $Repository 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    & gh release edit latest --repo $Repository --title "AutoDev latest · v$Version" --notes "由 main 分支 $Commit 构建。服务器使用固定 latest 地址下载并一键升级。"
}
else {
    & gh release create latest --repo $Repository --title "AutoDev latest · v$Version" --notes "由 main 分支 $Commit 构建。服务器使用固定 latest 地址下载并一键升级。"
}
if ($LASTEXITCODE -ne 0) { throw "创建或更新 GitHub Release 失败" }

& gh release upload latest --repo $Repository $LatestZip $ChecksumFile --clobber
if ($LASTEXITCODE -ne 0) { throw "上传 GitHub Release 产物失败" }

Write-Host "GitHub latest 发布完成：v$Version"
Write-Host "https://github.com/$Repository/releases/download/latest/autodev-hybrid-latest.zip"
