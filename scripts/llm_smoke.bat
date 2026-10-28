@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONUTF8=1
call "%~dp0_python.bat"
if not defined PY ( echo [ERROR] Python 3 not found. & pause & exit /b 1 )
echo Using Python: !PY!
"!PY!" -c "import fastapi, httpx, pydantic, dotenv" 2>nul || "!PY!" -m pip install -e backend
"!PY!" scripts\llm_smoke.py --provider deepseek
echo.
echo Done. Results are in data\runs\ . You can close this window.
pause
