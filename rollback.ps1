param([string]$InstallId)
if ($InstallId) {
    & (Join-Path $PSScriptRoot 'dub007.ps1') rollback --install-id $InstallId
} else {
    & (Join-Path $PSScriptRoot 'dub007.ps1') rollback --latest
}
exit $LASTEXITCODE
