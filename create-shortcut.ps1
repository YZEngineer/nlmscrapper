param([string]$ProjectDir)
$ErrorActionPreference = 'Stop'

$ProjectDir = $ProjectDir.TrimEnd('\') + '\'

$desktop = [Environment]::GetFolderPath('Desktop')
$lnkPath = Join-Path $desktop 'scrapeLm.exe.lnk'

$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($lnkPath)
$lnk.TargetPath = $ProjectDir + 'start.bat'
$lnk.WorkingDirectory = $ProjectDir
$lnk.IconLocation = $ProjectDir + 'ScrapLm.ico,0'
$lnk.WindowStyle = 7
$lnk.Description = 'scrapperNlm launcher'
$lnk.Save()

Write-Output ("SHORTCUT_OK: " + $lnkPath)
Write-Output ("TARGET: " + $lnk.TargetPath)
Write-Output ("ICON: " + $lnk.IconLocation)