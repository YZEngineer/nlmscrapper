@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

chcp 65001 >nul

if "%SCNLM_PORT%"=="" set "SCNLM_PORT=5000"

echo scrapperNlm durduruluyor (port %SCNLM_PORT%)...
set "KILLED="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%SCNLM_PORT%" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%p >nul 2>nul
    if not errorlevel 1 set "KILLED=1"
)
if defined KILLED (
    echo scrapperNlm durduruldu.
) else (
    echo Port %SCNLM_PORT% uzerinde calisan bir islem yok.
)
timeout /t 1 /nobreak >nul