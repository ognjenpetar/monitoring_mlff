@echo off
cd /d "%~dp0"

:: Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo Python nije pronađen. Instaliraj Python 3.8+ sa python.org
    pause
    exit /b 1
)

:: Install dependencies if needed
echo Proveravam zavisnosti...
python -m pip install -r requirements.txt --quiet

:: Run the app
echo Pokretam MLFF Monitoring...
python app.py
