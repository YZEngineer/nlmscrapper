@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

chcp 65001 >nul

echo ==========================================================
echo  scrapperNlm - Kurulum (Setup)
echo ==========================================================
echo.

rem --------------------------------------------------- [1/4] venv
if exist venv\Scripts\python.exe (
    echo [1/4] venv zaten var: venv\Scripts\python.exe
) else (
    echo [1/4] venv olusturuluyor...
    where py >nul 2>nul
    if errorlevel 1 (
        echo  py launcher bulunamadi, python deneyiliyor...
        python -m venv venv
    ) else (
        py -3.13 -m venv venv
        if errorlevel 1 (
            echo  py -3.13 basarisiz, python deneyiliyor...
            python -m venv venv
        )
    )
    if not exist venv\Scripts\python.exe (
        echo.
        echo  HATA: venv olusturulamadi.
        echo  Python 3.13 kurulu oldugundan emin olun:
        echo    https://www.python.org/downloads/
        echo  (kurulumda "Add python.exe to PATH" secenegini isaretleyin)
        pause
        exit /b 1
    )
)

rem --------------------------------------------------- [2/4] pip
echo [2/4] pip guncelleniyor...
".\venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :err

rem --------------------------------------------------- [3/4] requirements
echo [3/4] requirements.txt kuruluyor...
".\venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :err

rem --------------------------------------------------- [4/4] chromium
echo [4/4] Playwright Chromium kuruluyor...
".\venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto :err

echo.
echo  ==========================================================
echo  Kurulum tamamlandi.
echo.
echo  Bir sonraki adim (istege bagli): NotebookLM giris
echo    venv\Scripts\python.exe -m notebooklm login
echo.
echo  Uygulamayi baslatmak icin:  start.bat  (veya start.bat
echo  venv yoksa bu kurulumu otomatik calistirir).
echo  ==========================================================
pause
exit /b 0

:err
echo.
echo  HATA: Kurulum basarisiz oldu. Yukaridaki mesajlari kontrol edin.
pause
exit /b 1