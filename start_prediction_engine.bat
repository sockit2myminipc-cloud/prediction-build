@echo off
setlocal

REM Always run from this script's directory.
cd /d "%~dp0"

set "VENV_ACTIVATE=%CD%\.venv\Scripts\activate.bat"
set "OLLAMA_MODEL=qwen2.5:7b"

if not exist "%VENV_ACTIVATE%" (
  echo Virtual environment not found. Creating .venv ...
  where python >nul 2>&1
  if errorlevel 1 (
    echo [ERROR] Python was not found in PATH.
    echo Install Python 3.11+ and ensure "python" works in cmd.
    pause
    exit /b 1
  )

  python -m venv .venv
  if errorlevel 1 (
    echo [ERROR] Failed to create virtual environment.
    pause
    exit /b 1
  )

  echo Installing dependencies...
  call "%VENV_ACTIVATE%"
  pip install -r requirements.txt
  if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
  )

  REM Ensure path variable is available again outside prior call context.
  set "VENV_ACTIVATE=%CD%\.venv\Scripts\activate.bat"
)

if not exist "logs" mkdir "logs"

where ollama >nul 2>&1
if errorlevel 1 (
  echo [WARNING] Ollama is not installed or not in PATH.
  echo           Engine will fall back to regex entity extraction.
) else (
  echo Checking Ollama service...
  ollama list >nul 2>&1
  if errorlevel 1 (
    echo Starting Ollama service...
    start "Ollama Service" cmd /k "ollama serve"
    timeout /t 3 /nobreak >nul
  ) else (
    echo Ollama service is already running.
  )

  echo Ensuring Ollama model is available: %OLLAMA_MODEL%
  ollama list | findstr /i /c:"%OLLAMA_MODEL%" >nul
  if errorlevel 1 (
    echo Model not found locally. Pulling %OLLAMA_MODEL% ...
    ollama pull %OLLAMA_MODEL%
  ) else (
    echo Model is available.
  )
)

echo Starting prediction market engine...
start "Prediction Engine" cmd /k "cd /d "%CD%" && call "%VENV_ACTIVATE%" && python -m engines.prediction_markets.main --log-file logs\hourly_run.log"

echo Starting dashboard on http://localhost:8090 ...
start "Prediction Dashboard" cmd /k "cd /d "%CD%" && call "%VENV_ACTIVATE%" && python -m uvicorn shared.dashboard.app:app --host 0.0.0.0 --port 8090"

REM Give Uvicorn a moment to boot, then open browser.
ping -n 4 127.0.0.1 >nul
start "" "http://localhost:8090"

echo.
echo Launched:
echo   - Ollama service (if installed)
echo   - Engine window
echo   - Dashboard window
echo   - Browser at http://localhost:8090
echo.
echo Use stop_prediction_engine.bat to stop everything.
endlocal
