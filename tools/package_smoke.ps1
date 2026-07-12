param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [ValidateRange(5, 300)][int]$TimeoutSeconds = 60,
    [switch]$RequireBundledChromium
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$executablePath = (Resolve-Path -LiteralPath $Executable -ErrorAction Stop).Path
if (-not (Test-Path -LiteralPath $executablePath -PathType Leaf)) {
    throw "Packaged executable does not exist: $executablePath"
}

$versionInfo = (Get-Item -LiteralPath $executablePath).VersionInfo
if ([string]::IsNullOrWhiteSpace($versionInfo.FileVersion) -or
    [string]::IsNullOrWhiteSpace($versionInfo.ProductVersion) -or
    [string]::IsNullOrWhiteSpace($versionInfo.Comments)) {
    throw "Packaged executable is missing traceable version metadata: $executablePath"
}

function Stop-ProcessTree {
    param([Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process)

    $Process.Refresh()
    if ($Process.HasExited) {
        return
    }
    $taskkill = Join-Path $env:SystemRoot "System32\taskkill.exe"
    & $taskkill /PID $Process.Id /T /F | Out-Null
    $exitCode = $LASTEXITCODE
    $Process.Refresh()
    if ($exitCode -ne 0 -and -not $Process.HasExited) {
        throw "Failed to stop timed-out process tree $($Process.Id); taskkill exited $exitCode."
    }
}

function Invoke-SmokeProcess {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList
    )

    $process = Start-Process -FilePath $executablePath -ArgumentList $ArgumentList -PassThru -WindowStyle Hidden
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        Stop-ProcessTree -Process $process
        throw "$Label timed out after $TimeoutSeconds seconds."
    }
    $process.Refresh()
    if ($process.ExitCode -ne 0) {
        throw "$Label failed with exit code $($process.ExitCode)."
    }
}

$tempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$smokeRoot = [System.IO.Path]::GetFullPath((Join-Path $tempBase ("bili-package-smoke-" + [guid]::NewGuid().ToString("N"))))
if (-not $smokeRoot.StartsWith($tempBase, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to create smoke directory outside the system temporary directory: $smokeRoot"
}

$environmentNames = @(
    "APPDATA",
    "LOCALAPPDATA",
    "USERPROFILE",
    "QT_QPA_PLATFORM",
    "PYTHONUTF8",
    "PLAYWRIGHT_BROWSERS_PATH"
)
$previousEnvironment = @{}
foreach ($name in $environmentNames) {
    $previousEnvironment[$name] = [System.Environment]::GetEnvironmentVariable($name, "Process")
}

try {
    $appdata = Join-Path $smokeRoot "AppData\Roaming"
    $localappdata = Join-Path $smokeRoot "AppData\Local"
    $profile = Join-Path $smokeRoot "UserProfile"
    New-Item -ItemType Directory -Force -Path $appdata, $localappdata, $profile | Out-Null
    $env:APPDATA = $appdata
    $env:LOCALAPPDATA = $localappdata
    $env:USERPROFILE = $profile
    $env:QT_QPA_PLATFORM = "offscreen"
    $env:PYTHONUTF8 = "1"
    Remove-Item Env:PLAYWRIGHT_BROWSERS_PATH -ErrorAction SilentlyContinue

    Invoke-SmokeProcess -Label "Packaged --self-test" -ArgumentList @("--self-test")

    $playwrightOutput = Join-Path $smokeRoot "packaged-playwright.json"
    $quotedOutput = '"' + $playwrightOutput.Replace('"', '\"') + '"'
    Invoke-SmokeProcess -Label "Packaged Playwright smoke" -ArgumentList @("--playwright-smoke-output", $quotedOutput)
    if (-not (Test-Path -LiteralPath $playwrightOutput -PathType Leaf)) {
        throw "Packaged Playwright smoke did not write its result file."
    }
    $playwrightResult = Get-Content -LiteralPath $playwrightOutput -Encoding utf8 -Raw | ConvertFrom-Json
    if (-not ($playwrightResult.PSObject.Properties.Name -contains "ok") -or -not [bool]$playwrightResult.ok) {
        throw "Packaged Playwright smoke reported failure: $($playwrightResult.error)"
    }
    if ([string]$playwrightResult.page_url -ne "about:blank") {
        throw "Packaged Playwright smoke did not load about:blank."
    }
    if ($RequireBundledChromium -and [string]$playwrightResult.browser -ne "playwright-chromium") {
        throw "Packaged Playwright smoke did not use the bundled Chromium runtime."
    }

    Write-Host "Package smoke passed: $executablePath"
    Write-Host "Version: $($versionInfo.ProductVersion)"
}
finally {
    foreach ($name in $environmentNames) {
        [System.Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], "Process")
    }
    if (Test-Path -LiteralPath $smokeRoot) {
        $resolvedSmokeRoot = [System.IO.Path]::GetFullPath($smokeRoot)
        if (-not $resolvedSmokeRoot.StartsWith($tempBase, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove smoke directory outside the system temporary directory: $resolvedSmokeRoot"
        }
        Remove-Item -LiteralPath $resolvedSmokeRoot -Recurse -Force
    }
}
