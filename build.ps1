param(
    [switch]$Clean,
    [switch]$RequireClean,
    [string]$ExpectedCommit,
    [string]$ExpectedTag,
    [string]$Python,
    [string]$Dotnet
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

# The build environment is disposable by design. Never reuse a repository-level
# virtual environment whose contents may have drifted from the hash lock.
Remove-ScopedBuildDirectory -Name "build\.venv"

$systemPython = if ($Python) { (Resolve-Path -LiteralPath $Python).Path } else { (Get-Command python -ErrorAction Stop).Source }
$dotnetExe = if ($Dotnet) { (Resolve-Path -LiteralPath $Dotnet).Path } else { (Get-Command dotnet -ErrorAction Stop).Source }
$pythonVersion = Invoke-CapturedNative -FilePath $systemPython -ArgumentList @(
    "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
) -Step "Read Python version"
if ($pythonVersion -ne "3.13") {
    throw "The reproducible build requires Python 3.13; found $pythonVersion."
}
Invoke-CheckedNative -FilePath $systemPython -ArgumentList @("tools\verify_dependency_lock.py") -Step "Verify dependency locks"

$venvPython = Join-Path $Root "build\.venv\Scripts\python.exe"
Invoke-CheckedNative -FilePath $systemPython -ArgumentList @("-m", "venv", "build\.venv") -Step "Create clean virtual environment"
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw "Virtual environment Python was not created: $venvPython"
}

Invoke-CheckedNative -FilePath $venvPython -ArgumentList @(
    "-m", "pip", "install", "--require-hashes", "--only-binary=:all:", "-r", "requirements.txt"
) -Step "Install hash-locked runtime dependencies"
Invoke-CheckedNative -FilePath $venvPython -ArgumentList @("-m", "pip", "check") -Step "Check dependency consistency"

$version = Invoke-CapturedNative -FilePath $venvPython -ArgumentList @("-c", "from app import __version__; print(__version__)") -Step "Read application version"
if ($version -notmatch "^\d+\.\d+$") {
    throw "Application version must use exactly two numeric components; found '$version'."
}

$commit = Invoke-CapturedNative -FilePath "git" -ArgumentList @("rev-parse", "HEAD") -Step "Read Git commit"
if ($commit -notmatch "^[0-9a-fA-F]{40,64}$") {
    throw "Git returned an invalid commit id: $commit"
}
$dirtyOutput = Invoke-CapturedNative -FilePath "git" -ArgumentList @("status", "--porcelain", "--untracked-files=all") -Step "Check Git worktree state"
$isDirty = -not [string]::IsNullOrWhiteSpace($dirtyOutput)
if ($RequireClean -and $isDirty) {
    throw "Release build requires a clean Git worktree."
}
if (-not [string]::IsNullOrWhiteSpace($ExpectedCommit) -and $commit -ne $ExpectedCommit.ToLowerInvariant()) {
    throw "Build commit '$commit' does not match expected commit '$ExpectedCommit'."
}
if (-not [string]::IsNullOrWhiteSpace($ExpectedTag)) {
    if ($ExpectedTag -notmatch "^v\d+\.\d+$") {
        throw "Expected tag must use vMAJOR.MINOR: '$ExpectedTag'."
    }
    $tagCommit = Invoke-CapturedNative -FilePath "git" -ArgumentList @("rev-parse", "refs/tags/$ExpectedTag^{commit}") -Step "Resolve expected tag"
    if ($tagCommit -ne $commit) {
        throw "Tag '$ExpectedTag' resolves to '$tagCommit', not build commit '$commit'."
    }
    if ($ExpectedTag -ne "v$version") {
        throw "Tag '$ExpectedTag' does not match source version '$version'."
    }
}

if ([string]::IsNullOrWhiteSpace($env:SOURCE_DATE_EPOCH)) {
    $env:SOURCE_DATE_EPOCH = Invoke-CapturedNative -FilePath "git" -ArgumentList @("show", "-s", "--format=%ct", "HEAD") -Step "Read commit timestamp"
}

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

$env:BILI_VERSION_FILE = $versionInfoPath
$env:BILI_BUILD_METADATA = $buildInfoPath
$env:PYINSTALLER_CONFIG_DIR = Join-Path $Root "build\pyinstaller-cache"

# Dependency discovery must not pick up unrelated DLLs from developer tools on
# PATH (for example another application's ICU or FFmpeg build).
$previousPath = $env:PATH
$env:PATH = (Join-Path $env:SystemRoot "System32") + [System.IO.Path]::PathSeparator + $env:SystemRoot
try {
    Invoke-CheckedNative -FilePath $venvPython -ArgumentList @("-m", "PyInstaller", "--noconfirm", "--clean", "BiliDownloader.Backend.spec") -Step "Build Qt-free Python backend"
}
finally {
    $env:PATH = $previousPath
}

$artifact = Join-Path $Root "dist\BiliDownloader.Backend\BiliDownloader.Backend.exe"
if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
    throw "PyInstaller reported success but the expected artifact is missing: $artifact"
}

$artifactVersion = (Get-Item -LiteralPath $artifact).VersionInfo
if ($artifactVersion.FileVersion -ne $version) {
    throw "Built artifact has unexpected FileVersion '$($artifactVersion.FileVersion)'; expected '$version'."
}
if ($artifactVersion.ProductVersion -ne $version) {
    throw "Built artifact has unexpected ProductVersion '$($artifactVersion.ProductVersion)'; expected '$version'."
}
if ($artifactVersion.ProductName -cne "Bili Downloader Lite") {
    throw "Built artifact has unexpected ProductName '$($artifactVersion.ProductName)'."
}
if ($artifactVersion.OriginalFilename -ne "BiliDownloader.Backend.exe") {
    throw "Built artifact has unexpected OriginalFilename '$($artifactVersion.OriginalFilename)'."
}
if ([string]::IsNullOrWhiteSpace($artifactVersion.Comments) -or -not $artifactVersion.Comments.Contains($commit)) {
    throw "Built artifact does not contain the expected Git commit in its version metadata."
}
if ($RequireClean -and -not $artifactVersion.Comments.Contains("dirty=false")) {
    throw "Built artifact does not identify a clean release build in its version metadata."
}

Remove-ScopedBuildDirectory -Name "build\winui-publish"
Invoke-CheckedNative -FilePath $dotnetExe -ArgumentList @(
    "restore", "BiliDownloader.WinUI\BiliDownloader.WinUI.csproj", "--locked-mode", "-p:Platform=x64"
) -Step "Restore locked Microsoft Windows App SDK dependencies"
Invoke-CheckedNative -FilePath $dotnetExe -ArgumentList @(
    "publish", "BiliDownloader.WinUI\BiliDownloader.WinUI.csproj", "--no-restore", "-c", "Release",
    "-p:Platform=x64", "-p:SourceRevisionId=$commit", "-p:BuildDirty=$($isDirty.ToString().ToLowerInvariant())",
    "-o", "build\winui-publish"
) -Step "Publish self-contained WinUI 3 frontend"
$frontend = Join-Path $Root "build\winui-publish\BiliDownloader.v$version.exe"
$frontendVersion = (Get-Item -LiteralPath $frontend).VersionInfo
if ($frontendVersion.FileVersion -ne "$version.0.0" -or $frontendVersion.ProductVersion -ne $version) {
    throw "WinUI frontend version does not match backend $version."
}
$packageName = "BiliDownloader.v$version.win-x64"
Remove-ScopedBuildDirectory -Name "dist\$packageName"
Invoke-CheckedNative -FilePath $venvPython -ArgumentList @(
    "tools\assemble_windows_package.py", "--version", $version, "--frontend", "build\winui-publish",
    "--backend", "dist\BiliDownloader.Backend", "--metadata", $buildInfoPath, "--output", "dist\$packageName"
) -Step "Assemble and archive Windows package"
$dirtyLabel = if ($isDirty) { " dirty" } else { "" }
Write-Host "Build done: dist\$packageName.zip"
Write-Host "Version: $version  Git: $commit$dirtyLabel"
