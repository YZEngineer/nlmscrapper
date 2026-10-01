@echo off
cd /d "%~dp0"
py -3 -m venv venv
if errorlevel 1 exit /b %errorlevel%
venv\Scripts\python.exe -m pip install -r requirements.txt
