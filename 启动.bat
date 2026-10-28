@echo off
setlocal
cd /d "%~dp0"
title OSR-Control

set "PY="
where python >nul 2>nul
if not errorlevel 1 set "PY=python"
if not defined PY (
  where py >nul 2>nul
  if not errorlevel 1 set "PY=py -3"
)
if not defined PY goto nopy

%PY% -c "import sys;raise SystemExit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
if errorlevel 1 goto badpy

if not exist "webui.html" goto nohtml

%PY% webui.py
set "RC=%ERRORLEVEL%"
echo.
echo   [stopped] You can close this window now.
pause
exit /b %RC%

:nopy
echo.
echo   [ERROR] Python not found.
echo   Install Python 3.10+ from https://www.python.org/downloads/
echo   Remember to tick "Add Python to PATH" during setup.
echo.
echo   See the README.md / user guide for details.
pause
exit /b 1

:badpy
echo.
echo   [ERROR] Python is too old (need 3.10+), or it is the Microsoft Store stub.
echo   Install a real build from https://www.python.org/downloads/
pause
exit /b 1

:nohtml
echo.
echo   [ERROR] webui.html is missing - this folder is incomplete.
echo   Please unzip the whole folder again.
pause
exit /b 1
