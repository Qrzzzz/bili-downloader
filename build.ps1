param(
    [switch]$Clean,
    [switch]$OneFile,
    [switch]$SkipPlaywrightBrowserInstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = [System.IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path))
Set-Location -LiteralPath $Root

function Invoke-CheckedNative {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$Step
    )

    # Windows PowerShell 5.1 surfaces native stderr as ErrorRecord objects.
    # Tools such as PyInstaller legitimately write INFO lines there, so only
    # the native exit code is authoritative.
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $FilePath @ArgumentList
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne 0) {
        throw "$Step failed with exit code $exitCode."
    }
}

function Invoke-CapturedNative {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$Step
    )

    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $FilePath @ArgumentList
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne 0) {
        throw "$Step failed with exit code $exitCode."
    }
    return (($output | ForEach-Object { [string]$_ }) -join "`n").Trim()
}

function Remove-ScopedBuildDirectory {
    param([Parameter(Mandatory = $true)][string]$Name)

    $target = [System.IO.Path]::GetFullPath((Join-Path $Root $Name))
    $rootPrefix = $Root.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $target.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove path outside repository root: $target"
    }
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
    }
}

if ($Clean) {
    Remove-ScopedBuildDirectory -Name "build"
    Remove-ScopedBuildDirectory -Name "dist"
}

$systemPython = (Get-Command python -ErrorAction Stop).Source
$venvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    Invoke-CheckedNative -FilePath $systemPython -ArgumentList @("-m", "venv", ".venv") -Step "Create virtual environment"
}
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw "Virtual environment Python was not created: $venvPython"
}

Invoke-CheckedNative -FilePath $venvPython -ArgumentList @("-m", "pip", "install", "--upgrade", "pip") -Step "Upgrade pip"
Invoke-CheckedNative -FilePath $venvPython -ArgumentList @("-m", "pip", "install", "-r", "requirements.txt") -Step "Install runtime dependencies"

$bundledBrowserRoot = Join-Path $Root "ms-playwright"
if (-not $SkipPlaywrightBrowserInstall) {
    $env:PLAYWRIGHT_BROWSERS_PATH = $bundledBrowserRoot
    Invoke-CheckedNative -FilePath $venvPython -ArgumentList @("-m", "playwright", "install", "chromium") -Step "Install Playwright Chromium"
}
$browserSource = $null
if (Test-Path -LiteralPath $bundledBrowserRoot -PathType Container) {
    $browserSource = $bundledBrowserRoot
} elseif (-not [string]::IsNullOrWhiteSpace($env:PLAYWRIGHT_BROWSERS_PATH) -and
        (Test-Path -LiteralPath $env:PLAYWRIGHT_BROWSERS_PATH -PathType Container)) {
    $browserSource = [System.IO.Path]::GetFullPath($env:PLAYWRIGHT_BROWSERS_PATH)
}

Invoke-CheckedNative -FilePath $venvPython -ArgumentList @("-m", "compileall", "-q", "app", "tools") -Step "Compile Python sources"

$version = Invoke-CapturedNative -FilePath $venvPython -ArgumentList @("-c", "from app import __version__; print(__version__)") -Step "Read application version"
if ([string]::IsNullOrWhiteSpace($version)) {
    throw "Application version is empty."
}

$commit = Invoke-CapturedNative -FilePath "git" -ArgumentList @("rev-parse", "HEAD") -Step "Read Git commit"
if ($commit -notmatch "^[0-9a-fA-F]{40,64}$") {
    throw "Git returned an invalid commit id: $commit"
}
$dirtyOutput = Invoke-CapturedNative -FilePath "git" -ArgumentList @("status", "--porcelain", "--untracked-files=all") -Step "Check Git worktree state"
$isDirty = -not [string]::IsNullOrWhiteSpace($dirtyOutput)

$metadataDir = Join-Path $Root "build\metadata"
New-Item -ItemType Directory -Force -Path $metadataDir | Out-Null
$versionInfoPath = Join-Path $metadataDir "BiliDownloader.version"
$buildInfoPath = Join-Path $metadataDir "build-info.json"
$metadataArgs = @(
    "tools\write_version_info.py",
    "--version", $version,
    "--commit", $commit,
    "--version-file", $versionInfoPath,
    "--metadata-file", $buildInfoPath
)
if ($isDirty) {
    $metadataArgs += "--dirty"
}
Invoke-CheckedNative -FilePath $venvPython -ArgumentList $metadataArgs -Step "Generate build metadata"

$env:BILI_BUILD_ONEFILE = if ($OneFile) { "1" } else { "0" }
$env:BILI_ARTIFACT_BASENAME = "BiliDownloader.v$version"
$env:BILI_VERSION_FILE = $versionInfoPath
$env:BILI_BUILD_METADATA = $buildInfoPath
if ($null -ne $browserSource) {
    $env:BILI_BROWSER_ROOT = $browserSource
} else {
    Remove-Item Env:BILI_BROWSER_ROOT -ErrorAction SilentlyContinue
}
Invoke-CheckedNative -FilePath $venvPython -ArgumentList @("-m", "PyInstaller", "--noconfirm", "--clean", "BiliDownloader.spec") -Step "Build application with PyInstaller"

$artifact = if ($OneFile) {
    Join-Path $Root "dist\BiliDownloader.v$version.exe"
} else {
    Join-Path $Root "dist\BiliDownloader\BiliDownloader.v$version.exe"
}
if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
    throw "PyInstaller reported success but the expected artifact is missing: $artifact"
}

$artifactVersion = (Get-Item -LiteralPath $artifact).VersionInfo
if ([string]::IsNullOrWhiteSpace($artifactVersion.FileVersion) -or -not $artifactVersion.FileVersion.StartsWith($version)) {
    throw "Built artifact has unexpected FileVersion '$($artifactVersion.FileVersion)'; expected '$version'."
}
if ([string]::IsNullOrWhiteSpace($artifactVersion.Comments) -or -not $artifactVersion.Comments.Contains($commit)) {
    throw "Built artifact does not contain the expected Git commit in its version metadata."
}

$mode = if ($OneFile) { "onefile" } else { "onedir" }
$dirtyLabel = if ($isDirty) { " dirty" } else { "" }
Write-Host "Build done ($mode): $artifact"
Write-Host "Version: $version  Git: $commit$dirtyLabel"
