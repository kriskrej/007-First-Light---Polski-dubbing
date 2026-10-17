param(
    [Parameter(Position = 0)]
    [ValidateSet('doctor', 'index', 'extract', 'api-check', 'estimate', 'sources', 'prepare-poc', 'prepare-mission', 'prepare-reactive', 'prepare-native-library', 'encoder-check', 'poc', 'campaign', 'campaign-first-pass', 'campaign-status', 'coverage-status', 'campaign-install', 'audit-sync', 'repair-sync', 'install', 'rollback')]
    [string]$Command = 'doctor',
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython -PathType Leaf)) {
    & python -m venv (Join-Path $projectRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python virtual environment creation failed.' }
    & $projectPython -m pip install -r (Join-Path $projectRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
Push-Location -LiteralPath $projectRoot
try {
    & $projectPython -m dub007 $Command @Arguments
    $commandExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $commandExitCode
