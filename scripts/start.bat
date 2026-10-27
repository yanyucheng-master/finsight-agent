@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONUTF8=1
set "PY="
for %%P in (
  "%USERPROFILE%\anaconda3\python.exe"
  "%USERPROFILE%\miniconda3\python.exe"
  "%LOCALAPPDATA%\anaconda3\python.exe"
  "%ProgramData%\anaconda3\python.exe"
) do (
  if not defined PY if exist %%P set "PY=%%~P"
)
if not defined PY (
  for /f "delims=" %%I in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%I"
)
if not defined PY (
  echo [ERROR] Python 3.10+ not found.
  pause
  exit /b 1
)
echo Using Python: !PY!
"!PY!" -c "import finsight" 2>nul || "!PY!" -m pip install -e backend
echo Starting FinSight API at http://127.0.0.1:8000/docs
"!PY!" -m finsight.cli serve --port 8000
pause
