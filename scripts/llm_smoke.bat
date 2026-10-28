@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONUTF8=1
if not exist data\runs mkdir data\runs
call "%~dp0_python.bat"
if not defined PY ( echo [ERROR] Python 3 not found. & pause & exit /b 1 )
echo Using Python: !PY!
call "%~dp0_deps.bat"
if errorlevel 1 ( echo. & echo Dependency check failed. Please run scripts\diagnose.bat and send the result. & pause & exit /b 1 )
"!PY!" scripts\llm_smoke.py --provider deepseek > data\runs\smoke_console.log 2>&1
type data\runs\smoke_console.log
echo.
echo Done. Results are in data\runs\ . You can close this window.
pause
