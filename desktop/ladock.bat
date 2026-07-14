@echo off
setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
set "MAIN_PY=%SCRIPT_DIR%main.py"
set "PYTHON_EXE="

:: ── Python discovery (conda / venv / system) ─────────────────────────────────

:: Active conda environment takes priority
if defined CONDA_PREFIX (
    if exist "%CONDA_PREFIX%\python.exe" (
        set "PYTHON_EXE=%CONDA_PREFIX%\python.exe"
        goto check_pyside
    )
)

:: Active virtual environment
if defined VIRTUAL_ENV (
    if exist "%VIRTUAL_ENV%\Scripts\python.exe" (
        set "PYTHON_EXE=%VIRTUAL_ENV%\Scripts\python.exe"
        goto check_pyside
    )
)

:: User Miniconda / Anaconda
for %%D in (
    "%USERPROFILE%\miniconda3"
    "%USERPROFILE%\anaconda3"
    "%USERPROFILE%\miniforge3"
    "%USERPROFILE%\mambaforge"
) do (
    if exist "%%~D\python.exe" (
        set "PYTHON_EXE=%%~D\python.exe"
        goto check_pyside
    )
)

:: System-wide Miniconda / Anaconda
for %%D in (
    "%ProgramData%\miniconda3"
    "%ProgramData%\anaconda3"
    "%ProgramData%\miniforge3"
) do (
    if exist "%%~D\python.exe" (
        set "PYTHON_EXE=%%~D\python.exe"
        goto check_pyside
    )
)

:: Python Launcher (py.exe)
where py >nul 2>nul
if not errorlevel 1 (
    for /f "delims=" %%I in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do (
        set "PYTHON_EXE=%%I"
    )
    if defined PYTHON_EXE goto check_pyside
)

:: Generic python / python3 on PATH
for %%C in (python python3) do (
    where %%C >nul 2>nul
    if not errorlevel 1 (
        for /f "delims=" %%I in ('where %%C 2^>nul') do (
            :: Skip Windows Store stub
            echo %%I | find /I "WindowsApps\python" >nul
            if errorlevel 1 (
                set "PYTHON_EXE=%%I"
                goto check_pyside
            )
        )
    )
)

:: No Python found
echo ERROR: Python 3.10+ was not found.
echo.
echo Install Miniconda (recommended):
echo   https://docs.conda.io/en/latest/miniconda.html
echo.
echo Or install Python from python.org:
echo   https://www.python.org/downloads/
echo.
pause
exit /b 1

:: ── PySide6 check ─────────────────────────────────────────────────────────────
:check_pyside
"%PYTHON_EXE%" -c "import PySide6" >nul 2>nul
if errorlevel 1 (
    echo ERROR: PySide6 is not installed for:
    echo   %PYTHON_EXE%
    echo.
    echo Install all dependencies with:
    echo   "%PYTHON_EXE%" -m pip install -e "%SCRIPT_DIR%"
    echo.
    echo Or install PySide6 only:
    echo   "%PYTHON_EXE%" -m pip install PySide6
    echo.
    pause
    exit /b 1
)

:: ── Launch ────────────────────────────────────────────────────────────────────
"%PYTHON_EXE%" "%MAIN_PY%" %*
if errorlevel 1 (
    echo.
    echo LADOCK exited with an error. Review the message above.
    pause
    exit /b 1
)

endlocal
