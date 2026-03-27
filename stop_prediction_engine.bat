@echo off
setlocal

echo Stopping Prediction Engine and Dashboard...

REM Kill the windows started by start_prediction_engine.bat (titles set via start "Title" ...)
taskkill /F /FI "WINDOWTITLE eq Prediction Engine*" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Prediction Dashboard*" >nul 2>&1

REM Kill by command line in case titles changed or windows were renamed.
powershell -NoProfile -Command ^
  "$ErrorActionPreference='SilentlyContinue';" ^
  "Get-CimInstance Win32_Process | Where-Object { " ^
  "  ($_.Name -match 'python(.exe)?$') -and (" ^
  "    $_.CommandLine -match 'engines\.prediction_markets\.main' -or " ^
  "    $_.CommandLine -match 'uvicorn\s+shared\.dashboard\.app:app'" ^
  "  )" ^
  "} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"

REM Free port 8090 as extra safety for dashboard.
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8090 ^| findstr LISTENING') do (
  taskkill /F /PID %%p >nul 2>&1
)

set /p STOP_OLLAMA=Stop Ollama service too? (Y/N): 
if /I "%STOP_OLLAMA%"=="Y" (
  taskkill /F /IM ollama.exe >nul 2>&1
  echo Ollama stop requested.
) else (
  echo Ollama left running.
)

echo Done.
endlocal
