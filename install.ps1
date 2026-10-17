param([switch]$DryRun)
$installArguments = @('install')
if ($DryRun) { $installArguments += '--dry-run' }
& (Join-Path $PSScriptRoot 'dub007.ps1') @installArguments
exit $LASTEXITCODE
