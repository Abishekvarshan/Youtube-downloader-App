$ErrorActionPreference = 'Stop'
$appRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $appRoot

if (-not (Test-Path '.env')) {
    Copy-Item '.env.example' '.env'
    Write-Host 'Created local settings in .env.' -ForegroundColor Green
}

# Read simple KEY=value entries from .env without printing secrets.
Get-Content '.env' | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$' -and $Matches[1] -notmatch '^\s*#') {
        $name = $Matches[1]
        $value = $Matches[2].Trim('"', "'")
        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }
}

$dataRoot = Join-Path $appRoot 'data'
$env:LOCALAPPDATA = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { $dataRoot }

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw 'Python 3.11 or newer is required. Install it from python.org and enable Add Python to PATH.'
}
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    python -m venv .venv
}
$python = Join-Path $appRoot '.venv\Scripts\python.exe'
& $python -m pip install --disable-pip-version-check -r requirements-windows.txt
$env:PATH = (Join-Path $appRoot '.venv\Scripts') + ';' + $env:PATH
Write-Host 'Launching the Streamline desktop window. Close the window to stop the app.'
& $python desktop.py

