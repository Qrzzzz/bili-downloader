param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$ExpectedVersion,
    [Parameter(Mandatory = $true)][string]$ExpectedCommit
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($ExpectedVersion -notmatch "^\d+\.\d+$") {
    throw "ExpectedVersion must use exactly MAJOR.MINOR: '$ExpectedVersion'."
}
if ($ExpectedCommit -notmatch "^[0-9a-fA-F]{40}$") {
    throw "ExpectedCommit must be a full 40-character Git SHA."
}

$resolved = (Resolve-Path -LiteralPath $Executable).Path
$expectedName = "BiliDownloader.v$ExpectedVersion.exe"
if ([System.IO.Path]::GetFileName($resolved) -cne $expectedName) {
    throw "Unexpected artifact name '$([System.IO.Path]::GetFileName($resolved))'; expected '$expectedName'."
}

$metadata = (Get-Item -LiteralPath $resolved).VersionInfo
if ($metadata.FileVersion -cne $ExpectedVersion) {
    throw "FileVersion '$($metadata.FileVersion)' does not equal '$ExpectedVersion'."
}
if ($metadata.ProductVersion -cne $ExpectedVersion) {
    throw "ProductVersion '$($metadata.ProductVersion)' does not equal '$ExpectedVersion'."
}
if ($metadata.ProductName -cne "Bili Downloader Lite") {
    throw "Unexpected ProductName '$($metadata.ProductName)'."
}
if ($metadata.OriginalFilename -cne $expectedName) {
    throw "OriginalFilename '$($metadata.OriginalFilename)' does not equal '$expectedName'."
}
if ([string]::IsNullOrWhiteSpace($metadata.Comments) -or -not $metadata.Comments.Contains($ExpectedCommit.ToLowerInvariant())) {
    throw "PE Comments do not contain the exact build commit '$ExpectedCommit'."
}
if (-not $metadata.Comments.Contains("dirty=false")) {
    throw "PE Comments do not identify a clean release build."
}

[ordered]@{
    artifact = $expectedName
    file_version = $metadata.FileVersion
    product_version = $metadata.ProductVersion
    product_name = $metadata.ProductName
    original_filename = $metadata.OriginalFilename
    git_commit = $ExpectedCommit.ToLowerInvariant()
} | ConvertTo-Json
