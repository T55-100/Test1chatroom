@echo off

REM Start Chat Room Server
cd /d %~dp0

REM Check if virtual environment exists
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate virtual environment
call venv\Scripts\activate

REM Install dependencies
pip install -r requirements.txt >nul 2>&1

REM Start application
python app.py
