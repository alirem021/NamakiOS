@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\bootstrap.ps1" %*
if errorlevel 1 (
    echo Automatic setup or launch failed. Check the message above and run again.
    echo Diagnostic files: "%~dp0debug-report.txt" and "%~dp0debug-stacks.txt"
    pause
    exit /b 1
)
echo NamakiOS stopped. Diagnostic files: "%~dp0debug-report.txt" and "%~dp0debug-stacks.txt"
pause
