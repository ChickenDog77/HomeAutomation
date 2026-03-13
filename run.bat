@echo off
REM ── Sauna Tracker — quick start ──
cd /d "%~dp0"
if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
    call venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call venv\Scripts\activate.bat
)
echo Starting Sauna Tracker on http://0.0.0.0:8000
uvicorn app:app --host 0.0.0.0 --port 8000
