@echo off
REM ── Install Sauna Tracker as a Windows startup task ──
REM Run this script once as Administrator.

set "APP_DIR=%~dp0"
set "TASK_NAME=SaunaTracker"
set "PYTHON=%APP_DIR%venv\Scripts\pythonw.exe"
set "UVICORN=%APP_DIR%venv\Scripts\uvicorn.exe"

REM Ensure venv exists
if not exist "%APP_DIR%venv" (
    echo Virtual environment not found. Run run.bat first to create it.
    pause
    exit /b 1
)

REM Create a scheduled task that runs at logon
schtasks /create /tn "%TASK_NAME%" ^
    /tr "\"%UVICORN%\" app:app --host 0.0.0.0 --port 8000" ^
    /sc onlogon /rl highest ^
    /f

if %errorlevel% equ 0 (
    echo.
    echo Task "%TASK_NAME%" created successfully.
    echo The sauna tracker will auto-start on every logon.
    echo To remove:  schtasks /delete /tn "%TASK_NAME%" /f
) else (
    echo Failed to create task. Make sure you run this as Administrator.
)
pause
