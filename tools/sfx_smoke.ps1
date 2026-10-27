param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$Python = "build\.venv\Scripts\python.exe",
    [string]$OutputDirectory = "build\sfx-smoke"
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
& $Python tools/sfx_validation.py --executable $Executable --version $Version --output $OutputDirectory
if ($LASTEXITCODE -ne 0) { throw "SFX extraction or payload comparison failed." }
$evidence = Get-Content -LiteralPath (Join-Path $OutputDirectory "sfx-evidence.json") -Raw -Encoding UTF8 | ConvertFrom-Json
& "$PSScriptRoot\package_smoke.ps1" -Executable $evidence.frontend -OutputDirectory (Join-Path $OutputDirectory "package-smoke")
if ($LASTEXITCODE -ne 0) { throw "Extracted native package smoke failed." }
Write-Host "SFX extraction, payload manifest, and native package startup passed."
