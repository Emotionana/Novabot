@echo off
setlocal
title Novabot Desktop
cd /d "%~dp0"

set "VENV=%~dp0.venv"
set "PY=%VENV%\Scripts\python.exe"
set "PYW=%VENV%\Scripts\pythonw.exe"
set "READY=%VENV%\.novabot-ready"

if exist "%PYW%" if exist "%READY%" goto launch

if exist "%VENV%" (
    if not exist "%PYW%" (
        echo [error] .venv exists but pythonw.exe is missing.
        echo Delete the .venv folder and run this file again.
        pause
        exit /b 1
    )
) else (
    echo [setup] First run: creating .venv ...
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -m venv "%VENV%"
    ) else (
        python -m venv "%VENV%"
    )
    if not exist "%PYW%" (
        echo [error] Could not create .venv.
        echo Install Python 3.11+ from https://www.python.org/ and add it to PATH.
        pause
        exit /b 1
    )
)

"%PY%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
    echo [error] Python 3.11+ is required.
    echo Delete .venv, install a newer Python, and run this file again.
    pause
    exit /b 1
)

echo [setup] Installing dependencies. This may take a few minutes ...
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install -e .
if errorlevel 1 (
    echo [error] Dependency install failed. Check your network and try again.
    pause
    exit /b 1
)
> "%READY%" echo ready

:launch
echo [start] Launching Novabot ...
start "" "%PYW%" "%~dp0desktop.py"
exit /b 0
