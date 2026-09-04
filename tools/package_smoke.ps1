param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [ValidateRange(5, 300)][int]$TimeoutSeconds = 60,
    [string]$OutputDirectory = "build\package-smoke"
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$executablePath = (Resolve-Path -LiteralPath $Executable).Path
$package = Split-Path -Parent $executablePath
$metadata = Get-Content -LiteralPath (Join-Path $package "build-info.json") -Raw | ConvertFrom-Json
$version = $metadata.version
$frontendVersion = (Get-Item -LiteralPath $executablePath).VersionInfo
$backendVersion = (Get-Item -LiteralPath (Join-Path $package "BiliDownloader.Backend.exe")).VersionInfo
if ($frontendVersion.FileVersion -ne "$version.0.0" -or $frontendVersion.ProductVersion -ne $version -or
    $backendVersion.FileVersion -ne $version -or $backendVersion.OriginalFilename -ne "BiliDownloader.Backend.exe") {
    throw "Packaged frontend/backend PE versions differ."
}
if ([IO.Path]::GetFileName($executablePath) -cne "BiliDownloader.v$version.exe") { throw "Unexpected frontend name." }
if (-not $backendVersion.Comments.Contains($metadata.git_commit)) { throw "Backend build commit differs." }
$output = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $output | Out-Null
$profile = Join-Path $output ("profile-" + [guid]::NewGuid().ToString("N"))
$names = @("APPDATA", "LOCALAPPDATA", "BILI_BACKEND_PYTHON", "BILI_BACKEND_SOURCE", "BILI_ACCEPTANCE_OUTPUT")
$previous = @{}
foreach ($name in $names) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, "Process") }
try {
    $env:APPDATA = Join-Path $profile "Roaming"
    $env:LOCALAPPDATA = Join-Path $profile "Local"
    $env:BILI_ACCEPTANCE_OUTPUT = $output
    $env:BILI_BACKEND_PYTHON = $null
    $env:BILI_BACKEND_SOURCE = $null
    New-Item -ItemType Directory -Force -Path $env:APPDATA,$env:LOCALAPPDATA | Out-Null
    $startedAt = [DateTime]::UtcNow
    $process = Start-Process -FilePath $executablePath -ArgumentList "--self-test" -PassThru -WindowStyle Hidden
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        throw "Native package smoke timed out; PID $($process.Id). Evidence preserved in $output."
    }
    $process.Refresh()
    if ($process.ExitCode -ne 0) { throw "Native package smoke failed with exit code $($process.ExitCode)." }
    $evidencePath = Join-Path $output "native-evidence.json"
    if ((Get-Item -LiteralPath $evidencePath).LastWriteTimeUtc -lt $startedAt) { throw "Native evidence is stale." }
    $evidence = Get-Content -LiteralPath $evidencePath -Raw | ConvertFrom-Json
    if (-not $evidence.backend_connected -or -not $evidence.extends_content_into_titlebar -or
        $evidence.window_type -ne "Microsoft.UI.Xaml.Window" -or
        $evidence.titlebar_type -ne "Microsoft.UI.Xaml.Controls.TitleBar" -or
        $evidence.navigation_type -ne "Microsoft.UI.Xaml.Controls.NavigationView" -or
        $evidence.backdrop_type -ne "Microsoft.UI.Xaml.Media.MicaBackdrop" -or
        $evidence.settings_page -ne "BiliDownloader.WinUI.Views.SettingsPage") { throw "Native structure verification failed." }
    if ($evidence.themes -notcontains "Light" -or $evidence.themes -notcontains "Dark") { throw "Theme verification failed." }
    if (@($evidence.controls | Where-Object { $_.type -eq "Microsoft.UI.Xaml.Controls.InfoBar" }).Count -eq 0) { throw "InfoBar is missing." }
    if ($evidence.git_commit -ne $metadata.git_commit -or $evidence.build_dirty -ne $metadata.dirty.ToString().ToLowerInvariant()) { throw "WinUI assembly build identity differs from package." }
    Write-Host "Native package smoke passed: $executablePath"
    Write-Host "Evidence: $evidencePath"
}
finally {
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $previous[$name], "Process") }
}
