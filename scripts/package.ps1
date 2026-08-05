$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$DistDir = Join-Path $ProjectRoot "dist"
$Version = (Get-Content -Raw -LiteralPath (Join-Path $ProjectRoot "VERSION")).Trim()
$Output = Join-Path $DistDir "autodev-hybrid-$Version.zip"

New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
if (Test-Path -LiteralPath $Output) {
    Remove-Item -LiteralPath $Output -Force
}

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$Stream = [System.IO.File]::Open($Output, [System.IO.FileMode]::CreateNew)
$Archive = New-Object System.IO.Compression.ZipArchive($Stream, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    $Files = Get-ChildItem -LiteralPath $ProjectRoot -Recurse -File -Force | Where-Object {
        $Relative = [System.IO.Path]::GetRelativePath($ProjectRoot, $_.FullName).Replace('\', '/')
        $Relative -notmatch '^(\.git|\.venv|\.venv-runner|data|dist)/' -and
        $Relative -notmatch '(^|/)__pycache__/' -and
        $Relative -notmatch '\.pyc$' -and
        $Relative -ne '.env' -and
        $Relative -notmatch '(^|/)\.env\.runner$' -and
        $Relative -ne 'deploy/cloud/.env.production' -and
        $Relative -ne 'deploy/backend/.env.backend' -and
        $Relative -notmatch '^deploy/cloud/(data|backups)/' -and
        $Relative -notmatch '^deploy/backend/(data|backups)/' -and
        $Relative -notmatch '^deploy/cloud/secrets/.*\.txt$' -and
        $Relative -notmatch '^deploy/backend/secrets/.*\.txt$' -and
        $Relative -notmatch '^local-runner/secrets/.*\.txt$'
    }
    foreach ($File in $Files) {
        $Relative = [System.IO.Path]::GetRelativePath($ProjectRoot, $File.FullName).Replace('\', '/')
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $Archive, $File.FullName, $Relative, [System.IO.Compression.CompressionLevel]::Optimal
        ) | Out-Null
    }
}
finally {
    $Archive.Dispose()
    $Stream.Dispose()
}

Write-Host "部署包已生成：$Output"
