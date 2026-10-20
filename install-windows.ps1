$ErrorActionPreference = 'Stop'
$appRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $appRoot

if (-not (Test-Path '.venv\Scripts\python.exe')) {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        throw 'Python 3.11 or newer is required. Install it from python.org and enable Add Python to PATH.'
    }
    python -m venv .venv
}
$python = Join-Path $appRoot '.venv\Scripts\python.exe'
& $python -m pip install --disable-pip-version-check -r requirements-windows.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
if (-not (Test-Path '.env') -and (Test-Path '.env.example')) {
    Copy-Item '.env.example' '.env'
}

$exe = Join-Path $appRoot 'Streamline.exe'
$build = Join-Path $appRoot '.build'
$arguments = @(
    '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed',
    '--name', 'Streamline', '--icon', (Join-Path $appRoot 'assets\streamline.ico'),
    '--distpath', $appRoot, '--workpath', $build, '--specpath', $build,
    '--add-data', ((Join-Path $appRoot 'app\templates') + ';app\templates'),
    '--add-data', ((Join-Path $appRoot 'app\static') + ';app\static'),
    '--collect-all', 'imageio_ffmpeg', '--collect-all', 'webview',
    '--collect-submodules', 'app', 'desktop.py'
)
& $python @arguments
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $exe)) { throw 'The Streamline app build failed.' }

# Use the signed-in user's Desktop path so the shortcut replaces the existing
# launcher even when the shell reports a redirected OneDrive Desktop folder.
$desktop = Join-Path $env:USERPROFILE 'Desktop'
$shortcutPath = Join-Path $desktop 'Streamline.lnk'
if (Test-Path -LiteralPath $shortcutPath) { Remove-Item -LiteralPath $shortcutPath -Force }
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$scriptHost = Join-Path $env:WINDIR 'System32\wscript.exe'
$shortcut.TargetPath = $scriptHost
$shortcut.Arguments = '"' + (Join-Path $appRoot 'launch-windows.vbs') + '"'
$shortcut.WorkingDirectory = $appRoot
$shortcut.Description = 'Streamline YouTube Downloader'
$shortcut.IconLocation = (Join-Path $appRoot 'assets\streamline.ico') + ',0'
$shortcut.Save()
$savedShortcut = $shell.CreateShortcut($shortcutPath)
if ($savedShortcut.TargetPath -ne $scriptHost) { throw "Could not save the app launcher: $($savedShortcut.TargetPath)" }

Write-Host "Installed Streamline and updated the desktop shortcut: $shortcutPath" -ForegroundColor Green
