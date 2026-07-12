param(
    [switch]$Clean,
    [switch]$OneFile,
    [switch]$SkipPlaywrightBrowserInstall
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if ($Clean) {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue ".\build", ".\dist"
}

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    python -m venv .venv
}

.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $Root "ms-playwright"
if (-not $SkipPlaywrightBrowserInstall) {
    $installArgs = @("-m", "playwright", "install", "chromium")
    $install = Start-Process -FilePath ".\.venv\Scripts\python.exe" -ArgumentList $installArgs -PassThru -NoNewWindow
    if (-not $install.WaitForExit(600000)) {
        Stop-Process -Id $install.Id -Force -ErrorAction SilentlyContinue
        Get-Process node -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -like "*$Root*\.venv\Lib\site-packages\playwright\driver\node.exe" } |
            Stop-Process -Force -ErrorAction SilentlyContinue
        Write-Warning "Playwright Chromium install timed out. The app can still use system Chrome/Edge through Playwright, or rerun: .\.venv\Scripts\python.exe -m playwright install chromium"
    } elseif ($install.ExitCode -ne 0) {
        Write-Warning "Playwright Chromium install failed with exit code $($install.ExitCode). The app can still use system Chrome/Edge through Playwright."
    }
}

.\.venv\Scripts\python.exe -m compileall app

if ($OneFile) {
    .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --name BiliDownloader --icon assets\icon.ico app\main.py
} else {
    .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean BiliDownloader.spec
}

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

Write-Host "Build done: dist\BiliDownloader\BiliDownloader.exe"
