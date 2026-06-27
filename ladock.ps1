$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$mainPy = Join-Path $scriptDir 'main.py'
$pythonCandidates = @(
    (Join-Path $env:USERPROFILE 'miniconda3\python.exe'),
    (Join-Path $env:USERPROFILE 'anaconda3\python.exe')
)

$pythonExe = $pythonCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

function Test-PySide6 {
    param([string]$PythonCmd, [string[]]$PrefixArgs = @())
    & $PythonCmd @PrefixArgs -c "import PySide6" *> $null
    return ($LASTEXITCODE -eq 0)
}

if (-not $pythonExe) {
    $pyCmd = Get-Command py -ErrorAction SilentlyContinue
    if ($pyCmd) {
        if (-not (Test-PySide6 -PythonCmd $pyCmd.Source -PrefixArgs @('-3'))) {
            throw "PySide6 is not installed for `py -3`. Install with: py -3 -m pip install -e `"$scriptDir`""
        }
        & $pyCmd.Source -3 $mainPy @args
        exit $LASTEXITCODE
    }

    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        if (-not (Test-PySide6 -PythonCmd $pythonCmd.Source)) {
            throw "PySide6 is not installed for `python`. Install with: python -m pip install -e `"$scriptDir`""
        }
        & $pythonCmd.Source $mainPy @args
        exit $LASTEXITCODE
    }

    Write-Error 'Python 3 was not found. Install Python or Miniconda, then run again.'
}

if (-not (Test-PySide6 -PythonCmd $pythonExe)) {
    throw "PySide6 is not installed in $pythonExe. Install with: `"$pythonExe`" -m pip install -e `"$scriptDir`""
}

& $pythonExe $mainPy @args
exit $LASTEXITCODE
