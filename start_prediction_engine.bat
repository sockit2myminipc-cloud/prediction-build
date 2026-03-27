@echo off
setlocal

REM Always run from this script's directory.
cd /d "%~dp0"

set "VENV_ACTIVATE=%CD%\.venv\Scripts\activate.bat"
set "OLLAMA_MODEL=qwen2.5:7b"

if not exist "%VENV_ACTIVATE%" (
  echo [ERROR] Virtual environment not found at:
  echo         %VENV_ACTIVATE%
  echo.
  echo Create it first:
  echo   python -m venv .venv
  echo   .\.venv\Scripts\activate
  echo   pip install -r requirements.txt
  echo.
  pause
  exit /b 1
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
timeout /t 3 /nobreak >nul
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
