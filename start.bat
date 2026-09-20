@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

chcp 65001 >nul

rem ---------------------------------------------------------------- venv check
if not exist venv\Scripts\python.exe (
    echo venv not found. Running setup first...
    call "%~dp0setup.bat"
    if errorlevel 1 exit /b 1
)

rem ---------------------------------------------------------------- port check
set "PORT=5000"
set "PID="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5000" ^| findstr "LISTENING"') do set "PID=%%p"

if defined PID (
    echo scrapperNlm is ALREADY running on port 5000
    echo Opening browser...
    start "" "http://127.0.0.1:5000"
    exit /b 0
)

rem ---------------------------------------------------------------- launch (hidden background)
echo Starting scrapperNlm in background (port 5000)...

set "SCNLM_PORT=5000"
set "SCNLM_DEBUG=0"
powershell -NoProfile -Command "$env:SCNLM_PORT='5000'; $env:SCNLM_DEBUG='0'; Start-Process -FilePath '%~dp0venv\Scripts\python.exe' -ArgumentList 'app.py' -WorkingDirectory '%~dp0' -WindowStyle Hidden -RedirectStandardOutput '%~dp0app.log' -RedirectStandardError '%~dp0app_err.log'"

rem ---------------------------------------------------------------- health check
echo Waiting for server...
set "OK="
for /l %%i in (1,1,20) do (
    powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:5000/api/fb/groups' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; exit 1" >nul 2>nul
    if !errorlevel!==0 set "OK=1" & goto :ready
    timeout /t 1 /nobreak >nul
)

:ready
if defined OK (
    echo scrapperNlm is running:  http://127.0.0.1:5000
    echo Opening browser...
    start "" "http://127.0.0.1:5000"
    exit /b 0
)

echo.
echo   WARNING: Server did not respond within 20 seconds.
echo   Check app_err.log:
if exist app_err.log (
    echo   -----------------------------------------------------
    type app_err.log
    echo   -----------------------------------------------------
)
echo.
echo   If the app never starts, run setup.bat again or check the logs.
pause
exit /b 1