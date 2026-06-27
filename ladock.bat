@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "MAIN_PY=%SCRIPT_DIR%main.py"
set "PYTHON_EXE=%USERPROFILE%\miniconda3\python.exe"

if exist "%PYTHON_EXE%" goto run

set "PYTHON_EXE=%USERPROFILE%\anaconda3\python.exe"
if exist "%PYTHON_EXE%" goto run

set "PYTHON_EXE=%ProgramData%\miniconda3\python.exe"
if exist "%PYTHON_EXE%" goto run

set "PYTHON_EXE=%ProgramData%\anaconda3\python.exe"
if exist "%PYTHON_EXE%" goto run

where py >nul 2>nul
if not errorlevel 1 (
    py -3 -c "import PySide6" >nul 2>nul
    if errorlevel 1 goto missing_pyside_py
    py -3 "%MAIN_PY%" %*
    if errorlevel 1 goto run_failed
    goto end
)

where python >nul 2>nul
if not errorlevel 1 (
    for /f "delims=" %%I in ('where python 2^>nul') do set "WHERE_PYTHON=%%I"
    echo %WHERE_PYTHON% | find /I "WindowsApps\\python.exe" >nul
    if not errorlevel 1 goto windows_store_alias
    python -c "import PySide6" >nul 2>nul
    if errorlevel 1 goto missing_pyside_python
    python "%MAIN_PY%" %*
    if errorlevel 1 goto run_failed
    goto end
)

echo Python 3 was not found.
echo Install Python or Miniconda, then run again.
pause
exit /b 1

:windows_store_alias
echo Windows is resolving `python` to the Microsoft Store alias:
echo   %WHERE_PYTHON%
echo.
echo Install a real Python distribution for Windows first, then install dependencies:
echo   python -m pip install -e "%SCRIPT_DIR%"
echo.
echo Recommended: install Miniconda or Python.org Python.
pause
exit /b 1

:run
"%PYTHON_EXE%" -c "import PySide6" >nul 2>nul
if errorlevel 1 goto missing_pyside_known
"%PYTHON_EXE%" "%MAIN_PY%" %*
if errorlevel 1 goto run_failed
goto end

:missing_pyside_known
echo PySide6 is not installed in:
echo   %PYTHON_EXE%
echo.
echo Install dependencies with:
echo   "%PYTHON_EXE%" -m pip install -e "%SCRIPT_DIR%"
echo.
pause
exit /b 1

:missing_pyside_py
echo PySide6 is not installed for the Python launcher ^(`py -3`^).
echo.
echo Install dependencies with:
echo   py -3 -m pip install -e "%SCRIPT_DIR%"
echo.
pause
exit /b 1

:missing_pyside_python
echo PySide6 is not installed for `python`.
echo.
echo Install dependencies with:
echo   python -m pip install -e "%SCRIPT_DIR%"
echo.
pause
exit /b 1

:run_failed
echo.
echo LADOCK exited with an error. Review the message above.
pause
exit /b 1

:end
endlocal
