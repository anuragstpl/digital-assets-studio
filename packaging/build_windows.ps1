# Build the Windows app and installer.
#
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
#
# Produces:
#   dist\ArtaloDigiSuit\           the app folder
#   dist\ArtaloDigiSuit-<v>-windows-setup.exe   (if Inno Setup is installed)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "==> Installing build dependencies" -ForegroundColor Cyan
python -m pip install --upgrade pip | Out-Null
python -m pip install -r requirements.txt pyinstaller | Out-Null

$version = (python -c "from digital_assets_studio.config import APP_VERSION; print(APP_VERSION)").Trim()
Write-Host "==> Building Artalo Digi Suit $version" -ForegroundColor Cyan

if (Test-Path dist)  { Remove-Item dist  -Recurse -Force }
if (Test-Path build) { Remove-Item build -Recurse -Force }
pyinstaller packaging\das.spec --noconfirm

Write-Host "==> Verifying the build" -ForegroundColor Cyan
& "dist\ArtaloDigiSuit\ArtaloDigiSuit.exe" --selftest
if ($LASTEXITCODE -ne 0) { throw "The built app failed its self-test" }

$iscc = @(
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($iscc) {
  Write-Host "==> Building the installer" -ForegroundColor Cyan
  & $iscc "/DAppVersion=$version" packaging\installer.iss
  Write-Host "`nInstaller: dist\ArtaloDigiSuit-$version-windows-setup.exe" -ForegroundColor Green
} else {
  Write-Host "`nInno Setup not found, so only the app folder was built." -ForegroundColor Yellow
  Write-Host "Install it with:  winget install JRSoftware.InnoSetup" -ForegroundColor Yellow
}
Write-Host "App folder: dist\ArtaloDigiSuit\" -ForegroundColor Green
