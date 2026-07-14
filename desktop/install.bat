@echo off
setlocal EnableDelayedExpansion

:: LADOCK Desktop — Windows installer
:: Usage: install.bat [--rdkit]

set "SCRIPT_DIR=%~dp0"
set "INSTALL_RDKIT=0"
set "PYTHON_EXE="

for %%A in (%*) do (
    if "%%A"=="--rdkit" set "INSTALL_RDKIT=1"
    if "%%A"=="-h" goto usage
    if "%%A"=="--help" goto usage
)
goto discover_python

:usage
echo Usage: install.bat [--rdkit]
echo   --rdkit   Also install RDKit (for SMILES rendering)
exit /b 0

:: ── Python discovery ──────────────────────────────────────────────────────────
:discover_python
echo === LADOCK Desktop Installer ===
echo.

:: Active conda / venv first
if defined CONDA_PREFIX (
    if exist "%CONDA_PREFIX%\python.exe" (
        set "PYTHON_EXE=%CONDA_PREFIX%\python.exe"
        goto check_version
    )
)
if defined VIRTUAL_ENV (
    if exist "%VIRTUAL_ENV%\Scripts\python.exe" (
        set "PYTHON_EXE=%VIRTUAL_ENV%\Scripts\python.exe"
        goto check_version
    )
)

:: Conda installations
for %%D in (
    "%USERPROFILE%\miniconda3"
    "%USERPROFILE%\anaconda3"
    "%USERPROFILE%\miniforge3"
    "%USERPROFILE%\mambaforge"
    "%ProgramData%\miniconda3"
    "%ProgramData%\anaconda3"
) do (
    if exist "%%~D\python.exe" (
        set "PYTHON_EXE=%%~D\python.exe"
        goto check_version
    )
)

:: Python launcher
where py >nul 2>nul
if not errorlevel 1 (
    for /f "delims=" %%I in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%I"
    if defined PYTHON_EXE goto check_version
)

:: Generic python on PATH
where python >nul 2>nul
if not errorlevel 1 (
    for /f "delims=" %%I in ('where python 2^>nul') do (
        echo %%I | find /I "WindowsApps\python" >nul
        if errorlevel 1 (
            set "PYTHON_EXE=%%I"
            goto check_version
        )
    )
)

echo ERROR: Python 3.10+ not found.
echo.
echo Install Miniconda (recommended):
echo   https://docs.conda.io/en/latest/miniconda.html
echo.
echo Or install Python from python.org:
echo   https://www.python.org/downloads/
echo.
pause
exit /b 1

:check_version
for /f "delims=" %%V in ('"%PYTHON_EXE%" -c "import sys; print(sys.version_info >= (3,10))" 2^>nul') do set "PY_OK=%%V"
if not "%PY_OK%"=="True" (
    echo ERROR: Python 3.10+ required. Found an older version at:
    echo   %PYTHON_EXE%
    echo.
    echo Install Python 3.10 or later.
    pause
    exit /b 1
)

echo Using Python: %PYTHON_EXE%
"%PYTHON_EXE%" --version
echo.

:: ── Install dependencies ──────────────────────────────────────────────────────
:: RDKit is now a core dependency (used by the preparation engine), so a plain
:: install already includes it. The --rdkit flag is kept for backward compat.
echo Installing LADOCK and its dependencies (including RDKit)...
"%PYTHON_EXE%" -m pip install -e "%SCRIPT_DIR%"
if errorlevel 1 (
    echo.
    echo ERROR: pip install failed. See output above.
    pause
    exit /b 1
)

:: ── Optional: desktop shortcut ────────────────────────────────────────────────
echo.
set "SHORTCUT=%USERPROFILE%\Desktop\LADOCK.bat"
(
    echo @echo off
    echo cd /d "%SCRIPT_DIR%"
    echo call "%SCRIPT_DIR%ladock.bat" %%*
) > "%SHORTCUT%"
echo Desktop shortcut created: %SHORTCUT%

echo.
echo === Installation complete! ===
echo.
echo Launch LADOCK with:
echo   Double-click LADOCK.bat on your Desktop
echo   OR: "%SCRIPT_DIR%ladock.bat"
echo.
pause
endlocal
