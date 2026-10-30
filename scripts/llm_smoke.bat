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
rem The script prints live progress and also writes data\runs\smoke_console.log itself
"!PY!" scripts\llm_smoke.py --provider deepseek --budget 5
set "RC=!errorlevel!"
echo.
if not "!RC!"=="0" echo [WARN] Some checks failed or fell back to template. See data\runs\smoke_summary.json
echo Done. Results are in data\runs\ . You can close this window.
pause
