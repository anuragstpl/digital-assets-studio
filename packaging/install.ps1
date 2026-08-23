# Install Digital Assets Studio from source on Windows.
#
#   powershell -ExecutionPolicy Bypass -File packaging\install.ps1
#
# Creates a virtual environment, installs everything, and adds Start Menu and
# desktop shortcuts. No admin rights needed.

$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent
Set-Location $repo

function Find-Python {
  foreach ($v in @("3.12", "3.11", "3.10")) {
    $p = & py -$v -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $p) { return $p.Trim() }
  }
  $p = (Get-Command python -ErrorAction SilentlyContinue).Source
  if ($p) {
    $ok = & $p -c "import sys; print(1 if (3,10) <= sys.version_info < (3,13) else 0)"
    if ($ok.Trim() -eq "1") { return $p }
  }
  return $null
}

$python = Find-Python
if (-not $python) {
  Write-Host "Python 3.10, 3.11 or 3.12 is required and none was found." -ForegroundColor Red
  Write-Host "Install it with:  winget install Python.Python.3.12" -ForegroundColor Yellow
  exit 1
}
Write-Host "==> Using $python" -ForegroundColor Cyan

if (-not (Test-Path .venv)) { & $python -m venv .venv }
$venvPy = Join-Path $repo ".venv\Scripts\python.exe"

Write-Host "==> Installing dependencies (this takes a couple of minutes)" -ForegroundColor Cyan
& $venvPy -m pip install --upgrade pip | Out-Null
& $venvPy -m pip install -r requirements.txt

Write-Host "==> Checking the install" -ForegroundColor Cyan
& $venvPy run.py --selftest
if ($LASTEXITCODE -ne 0) { throw "The install did not verify" }

# a launcher that starts the app without a console window
$vbs = Join-Path $repo "Digital Assets Studio.vbs"
@"
Set s = CreateObject("WScript.Shell")
s.CurrentDirectory = "$repo"
s.Run """$repo\.venv\Scripts\pythonw.exe"" ""$repo\run.py""", 0, False
"@ | Set-Content -Encoding ASCII $vbs

$shell = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Desktop"))) {
  $lnk = $shell.CreateShortcut((Join-Path $dir "Digital Assets Studio.lnk"))
  $lnk.TargetPath = $vbs
  $lnk.WorkingDirectory = $repo
  $lnk.IconLocation = Join-Path $repo "packaging\icon.ico"
  $lnk.Description = "One suite. Every digital asset."
  $lnk.Save()
}

Write-Host "`nInstalled." -ForegroundColor Green
Write-Host "Start it from the Start Menu, the desktop shortcut, or:  .\.venv\Scripts\python.exe run.py"
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  Write-Host "`nffmpeg was not found. Video and audio steps need it:" -ForegroundColor Yellow
  Write-Host "    winget install Gyan.FFmpeg" -ForegroundColor Yellow
}
