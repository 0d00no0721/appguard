param(
    [string]$Python = "D:\python\python.exe",
    [string]$Iscc = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    [switch]$SkipTests
)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root
$env:PYTHONIOENCODING = "utf-8"

if (-not (Test-Path $Python)) { throw "Python not found: $Python" }
if (-not (Test-Path $Iscc)) { throw "Inno Setup ISCC.exe not found: $Iscc" }

if (-not $SkipTests) {
    Write-Host "=== tests ==="
    & $Python "tests\run_all.py"
    if ($LASTEXITCODE -ne 0) { throw "tests failed" }
    & node --check "web\app.js"
    if ($LASTEXITCODE -ne 0) { throw "node --check failed" }
}

Write-Host "=== PyInstaller ==="
& $Python -m PyInstaller "packaging\AppGuard.spec" --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$Dist = Join-Path $Root "dist\AppGuard"
if (-not (Test-Path (Join-Path $Dist "AppGuard.exe"))) { throw "AppGuard.exe not built" }

Write-Host "=== Inno Setup ==="
& $Iscc "packaging\AppGuard.iss"
if ($LASTEXITCODE -ne 0) { throw "ISCC failed" }

$Setup = Join-Path $Root "dist\installer\AppGuardSetup-0.4.0-x64.exe"
if (-not (Test-Path $Setup)) { throw "setup not found: $Setup" }
$Hash = (Get-FileHash -Algorithm SHA256 $Setup).Hash.ToLower()
"$Hash  AppGuardSetup-0.4.0-x64.exe" | Set-Content (Join-Path $Root "dist\installer\AppGuardSetup-0.4.0-x64.exe.sha256") -Encoding ascii

Write-Host ""
Write-Host "Built:"
Write-Host "  $Dist\AppGuard.exe"
Write-Host "  $Setup"
Write-Host "  SHA256: $Hash"
