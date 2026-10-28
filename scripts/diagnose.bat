@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONUTF8=1
if not exist data\runs mkdir data\runs
call "%~dp0_python.bat"
if not defined PY (
  echo [ERROR] Python 3 not found. > data\runs\diagnose_console.log
  type data\runs\diagnose_console.log
  pause
  exit /b 1
)
echo Using Python: !PY!
"!PY!" scripts\diagnose_env.py > data\runs\diagnose_console.log 2>&1
type data\runs\diagnose_console.log
echo.
echo Done. You can close this window.
pause
