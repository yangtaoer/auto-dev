param(
    [string]$RepositoryPath = (Get-Location).Path,
    [string]$MavenProfile = ''
)

$ErrorActionPreference = 'Stop'
$Repository = (Resolve-Path -LiteralPath $RepositoryPath).Path
$Pom = Join-Path $Repository 'pom.xml'
if (-not (Test-Path -LiteralPath $Pom -PathType Leaf)) {
    throw '接口程序缺少 pom.xml'
}
$Command = Get-Command mvn.cmd -ErrorAction SilentlyContinue
$Maven = if ($Command) { $Command.Source } else { 'C:\tool\apache-maven-3.9.16\bin\mvn.cmd' }
if (-not (Test-Path -LiteralPath $Maven -PathType Leaf)) {
    throw '未找到 Maven 构建程序'
}
$Arguments = @('-f', $Pom, '-DskipTests', 'clean', 'package')
if ($MavenProfile) {
    $Arguments += @('-P', $MavenProfile)
}
& $Maven @Arguments
if ($LASTEXITCODE -ne 0) {
    throw "接口程序 Maven 构建失败，退出码 $LASTEXITCODE"
}
