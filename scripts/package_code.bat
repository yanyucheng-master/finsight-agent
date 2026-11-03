@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONUTF8=1
rem Build the "key code" zip for the competition: dist\FinSight-Agent-code-YYYYMMDD.zip
rem Excludes .env / .venv / node_modules / .repo / data\runs / annual-report PDFs, and aborts if any API key is found.
call "%~dp0_python.bat"
if not defined PY ( echo [ERROR] Python 3 not found. & pause & exit /b 1 )
"!PY!" scripts\package_code.py %*
set "RC=!errorlevel!"
echo.
if "!RC!"=="0" ( echo Done. The zip is in the dist folder. ) else ( echo [FAILED] See the messages above. No zip was written if a key was found. )
pause
exit /b !RC!
