@echo off
cd /d "%~dp0"
set "DIR=%CD%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws=New-Object -ComObject WScript.Shell; $lnk=$ws.CreateShortcut([Environment]::GetFolderPath('Desktop')+'\scrapperNlm.lnk'); $lnk.TargetPath='%DIR%\start.bat'; $lnk.WorkingDirectory='%DIR%'; if (Test-Path '%DIR%\ScrapLm.ico') { $lnk.IconLocation='%DIR%\ScrapLm.ico,0' }; $lnk.Description='scrapperNlm launcher'; $lnk.Save(); Write-Host 'OK: Desktop\scrapperNlm.lnk created'"
pause