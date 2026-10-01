# Native arguments are passed as an array; no shell command string is constructed.
$ErrorActionPreference = 'Stop'
$cliArgs = @($args)
$pythonCandidates = @(
    $env:ESYS_CLI_PYTHON,
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe')
)
$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if ($pythonCommand) { $pythonCandidates += $pythonCommand.Source }
$pythonExe = $pythonCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1
if (-not $pythonExe) {
    Write-Output '{"schema_version":1,"status":"error","error":"Python 3.11+ required; set ESYS_CLI_PYTHON to its executable"}'
    exit 2
}
$originalPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = $PSScriptRoot
    & $pythonExe -m esys_cli @cliArgs
    $cliExitCode = $LASTEXITCODE
} finally {
    $env:PYTHONPATH = $originalPythonPath
}
exit $cliExitCode
