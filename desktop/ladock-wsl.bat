@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "MAIN_PY=%SCRIPT_DIR%\main.py"
set "WSL_SH=%SCRIPT_DIR%\ladock-wsl.sh"
set "WSL_EXE=%SystemRoot%\System32\wsl.exe"
set "WSL_DISTRO=%LADOCK_WSL_DISTRO%"

if not exist "%WSL_EXE%" goto missing_wsl
if not exist "%MAIN_PY%" goto missing_main
if not exist "%WSL_SH%" goto missing_sh

set "DRIVE=%SCRIPT_DIR:~0,1%"
set "REST=%SCRIPT_DIR:~2%"
set "REST=%REST:\=/%"

if /I "%DRIVE%"=="A" set "DRIVE_LOWER=a"
if /I "%DRIVE%"=="B" set "DRIVE_LOWER=b"
if /I "%DRIVE%"=="C" set "DRIVE_LOWER=c"
if /I "%DRIVE%"=="D" set "DRIVE_LOWER=d"
if /I "%DRIVE%"=="E" set "DRIVE_LOWER=e"
if /I "%DRIVE%"=="F" set "DRIVE_LOWER=f"
if /I "%DRIVE%"=="G" set "DRIVE_LOWER=g"
if /I "%DRIVE%"=="H" set "DRIVE_LOWER=h"
if /I "%DRIVE%"=="I" set "DRIVE_LOWER=i"
if /I "%DRIVE%"=="J" set "DRIVE_LOWER=j"
if /I "%DRIVE%"=="K" set "DRIVE_LOWER=k"
if /I "%DRIVE%"=="L" set "DRIVE_LOWER=l"
if /I "%DRIVE%"=="M" set "DRIVE_LOWER=m"
if /I "%DRIVE%"=="N" set "DRIVE_LOWER=n"
if /I "%DRIVE%"=="O" set "DRIVE_LOWER=o"
if /I "%DRIVE%"=="P" set "DRIVE_LOWER=p"
if /I "%DRIVE%"=="Q" set "DRIVE_LOWER=q"
if /I "%DRIVE%"=="R" set "DRIVE_LOWER=r"
if /I "%DRIVE%"=="S" set "DRIVE_LOWER=s"
if /I "%DRIVE%"=="T" set "DRIVE_LOWER=t"
if /I "%DRIVE%"=="U" set "DRIVE_LOWER=u"
if /I "%DRIVE%"=="V" set "DRIVE_LOWER=v"
if /I "%DRIVE%"=="W" set "DRIVE_LOWER=w"
if /I "%DRIVE%"=="X" set "DRIVE_LOWER=x"
if /I "%DRIVE%"=="Y" set "DRIVE_LOWER=y"
if /I "%DRIVE%"=="Z" set "DRIVE_LOWER=z"

if not defined DRIVE_LOWER goto bad_path

set "SCRIPT_DIR_WSL=/mnt/%DRIVE_LOWER%%REST%"
set "MAIN_PY_WSL=%SCRIPT_DIR_WSL%/main.py"
set "WSL_SH_WSL=%SCRIPT_DIR_WSL%/ladock-wsl.sh"

set "WSL_ARGS="
if defined WSL_DISTRO set "WSL_ARGS=-d %WSL_DISTRO%"

"%WSL_EXE%" %WSL_ARGS% bash "%WSL_SH_WSL%" "%SCRIPT_DIR_WSL%" "%MAIN_PY_WSL%"
if errorlevel 1 goto run_failed
goto end

:missing_wsl
echo WSL was not found on this Windows system.
echo Install WSL Ubuntu first, then run this launcher again.
pause
exit /b 1

:missing_main
echo main.py was not found:
echo   %MAIN_PY%
pause
exit /b 1

:missing_sh
echo ladock-wsl.sh was not found:
echo   %WSL_SH%
pause
exit /b 1

:bad_path
echo Failed to convert the Windows project path into a WSL path.
echo Project path:
echo   %SCRIPT_DIR%
pause
exit /b 1

:run_failed
echo.
echo LADOCK WSL launcher exited with an error. Review the message above.
pause
exit /b 1

:end
endlocal
