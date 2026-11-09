@echo off
setlocal
title Stop Novabot
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"
set "PORT=%~1"
if not defined PORT (
    if exist "%PY%" (
        for /f "delims=" %%p in ('"%PY%" "%~dp0scripts\get_port.py" 2^>nul') do set "PORT=%%p"
    )
)
if not defined PORT set "PORT=19100"

echo Stopping Novabot on port %PORT% ...

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
    taskkill /T /F /PID %%a >nul 2>&1
)

powershell -NoLogo -NoProfile -NonInteractive -Command ^
  "$me=$PID; $pids=@(Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $me -and $_.Name -match '^python' -and $_.CommandLine -match 'uvicorn[ ]+app\.main' } | Select-Object -ExpandProperty ProcessId); foreach ($p in $pids) { taskkill /T /F /PID $p | Out-Null }"

ping -n 3 127.0.0.1 >nul
powershell -NoLogo -NoProfile -NonInteractive -Command ^
  "$left=@(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python' -and $_.CommandLine -match 'uvicorn[ ]+app\.main' }); if ($left.Count -gt 0) { $left | ForEach-Object { taskkill /T /F /PID $_.ProcessId | Out-Null }; exit 1 }"

if errorlevel 1 (
    echo Some Novabot processes were still running and were killed.
) else (
    echo Novabot stopped.
)
exit /b 0
