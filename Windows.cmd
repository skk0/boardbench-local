@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  where py >nul 2>nul
  if errorlevel 1 (python -m venv .venv) else (py -3 -m venv .venv)
  if errorlevel 1 goto failure
)
.venv\Scripts\python.exe -c "import aiohttp, serial, esptool, keyring, webview" >nul 2>nul
if errorlevel 1 (
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 goto failure
)
.venv\Scripts\python.exe app.py %*
if errorlevel 1 goto failure
exit /b 0
:failure
echo Install Python 3.12 and check the messages above.
pause
exit /b 1
