import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("pwsh"), "PowerShell unavailable")
class AppDependencyBuildTests(unittest.TestCase):
    def test_offline_dependency_and_manifests_restored_on_success_and_failure(self):
        script = Path(__file__).resolve().parents[1] / "local-runner/project-scripts/app-frontend-build.ps1"
        for failed in (False, True):
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                front, source = root / "front", root / "source"
                front.mkdir()
                dependencies = {"wechart_client": "2.0.2-fix-data", "thpush-lib": "1.0.8"}
                for name, version in dependencies.items():
                    dependency = source / "node_modules" / name
                    dependency.mkdir(parents=True)
                    (dependency / "package.json").write_text(json.dumps({"name": name, "version": version}))
                manifest = json.dumps({"dependencies": dependencies}).encode()
                (front / "package.json").write_bytes(manifest)
                (front / "package-lock.json").write_bytes(b'{"original":true}')
                fake = root / "npm.ps1"
                fake.write_text('''
if ($args[0] -eq 'pack') {
    $target = (Get-Location).Path
    [IO.File]::WriteAllText((Join-Path $target 'module.tgz'), ('verified dependency ' + (Split-Path $target -Leaf)))
    Write-Output '[{"filename":"module.tgz"}]'
    $global:LASTEXITCODE = 0
} elseif ($args[0] -eq 'install') {
    $manifest = Get-Content package.json -Raw | ConvertFrom-Json
    if (-not $manifest.dependencies.wechart_client.StartsWith('file:')) { throw 'registry fallback forbidden' }
    if (-not $manifest.dependencies.'thpush-lib'.StartsWith('file:')) { throw 'internal registry fallback forbidden' }
    [IO.File]::WriteAllText((Join-Path (Get-Location).Path 'package-lock.json'), '{}')
    $global:LASTEXITCODE = FAIL_CODE
} else { $global:LASTEXITCODE = 0 }
'''.replace('FAIL_CODE', '1' if failed else '0'), encoding="utf-8")
                result = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-File", str(script),
                                         "-FrontendRoot", str(front), "-SourceRoot", str(source), "-Npm", str(fake),
                                         "-CacheRoot", str(root / "cache")], capture_output=True, text=True, encoding="utf-8", errors="replace")
                self.assertEqual(result.returncode == 0, not failed, result.stdout + result.stderr)
                self.assertEqual((front / "package.json").read_bytes(), manifest)
                self.assertEqual((front / "package-lock.json").read_bytes(), b'{"original":true}')
                self.assertEqual(len(list((root / "cache").glob("*.tgz"))), 2)
                self.assertEqual(list((root / "cache").glob("prepare-*")), [])
