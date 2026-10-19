@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

chcp 65001 >nul

echo ============================================
echo  scrapperNlm - Setup
echo ============================================

rem ---------------------------------------------------------------- 1) Python bul
set "PY="
where py >nul 2>nul
if not errorlevel 1 (
    set "PY=py -3"
) else (
    where python >nul 2>nul
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    echo.
    echo [ERROR] Python bulunamadi.
    echo Python 3.13'i suradan kurun: https://www.python.org/downloads/
    echo Kurulumda "Add python.exe to PATH" kutusunu isaretleyin.
    echo Sonra bu setup'i tekrar calistirin.
    echo.
    pause
    exit /b 1
)

rem ---------------------------------------------------------------- 2) venv
if not exist venv\Scripts\python.exe (
    echo [1/5] venv olusturuluyor...
    %PY% -m venv venv
    if errorlevel 1 (
        echo [ERROR] venv olusturulamadi.
        pause
        exit /b 1
    )
) else (
    echo [1/5] venv zaten mevcut - atlaniyor.
)

set "VPY=%~dp0venv\Scripts\python.exe"

rem ---------------------------------------------------------------- 3) bagimliliklar
echo [2/5] Bagimliliklar kuruluyor (requirements.txt)...
"%VPY%" -m pip install --upgrade pip
if errorlevel 1 (
    echo [ERROR] pip guncellenemedi.
    pause
    exit /b 1
)
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] pip install basarisiz oldu.
    pause
    exit /b 1
)

rem ---------------------------------------------------------------- 4) Playwright Chromium
echo [3/5] Playwright Chromium kuruluyor (ilk seferde uzun surebilir)...
"%VPY%" -m playwright install chromium
if errorlevel 1 (
    echo [ERROR] Playwright kurulamadi.
    pause
    exit /b 1
)

rem ---------------------------------------------------------------- 5) .env
echo [4/5] .env kontrol ediliyor...
if not exist .env (
    if exist .env.example (
        copy /y .env.example .env >nul
        echo   .env, .env.example dosyasindan kopyalandi.
        echo   Simdi .env icindeki SOCIALAPIS_TOKEN degerini kendi token'ini girin.
    ) else (
        echo   .env.example yok - .env olusturulmadi.
    )
) else (
    echo   .env zaten mevcut - atlaniyor.
)

rem ---------------------------------------------------------------- 6) kisa yol
echo [5/5] Masaustu kisa yolu olusturuluyor...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create-shortcut.ps1" -ProjectDir "%~dp0"

echo.
echo ============================================
echo  Setup tamamlandi.
echo  Simdi start.bat'i calistirin (veya masaustu kisa yolu).
echo  NotebookLM girisini ilk acilista uygulama icinden yapin.
echo ============================================
echo.
pause
exit /b 0