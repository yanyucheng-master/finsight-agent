@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONUTF8=1
call "%~dp0_python.bat"
if not defined PY ( echo [ERROR] Python 3 not found. & pause & exit /b 1 )
echo Using Python: !PY!
call "%~dp0_deps.bat"
if errorlevel 1 ( pause & exit /b 1 )
echo Starting FinSight at http://127.0.0.1:8000  (the browser opens automatically; close this window to stop)
"!PY!" -m finsight.cli serve --port 8000 --open
pause
