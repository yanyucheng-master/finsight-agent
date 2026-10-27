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
  "%LOCALAPPDATA%\miniconda3\python.exe"
  "%ProgramData%\anaconda3\python.exe"
  "%ProgramData%\miniconda3\python.exe"
) do (
  if not defined PY if exist %%P set "PY=%%~P"
)
if not defined PY (
  for /f "delims=" %%I in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%I"
)
if not defined PY (
  for /f "delims=" %%I in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%I"
)
if not defined PY (
  echo [ERROR] Python 3 not found. Open "Anaconda PowerShell Prompt" and run:
  echo   cd $HOME\Projects\finsight-agent
  echo   python scripts\collect_snapshots.py --symbols-file scripts\demo_symbols.txt
  pause
  exit /b 1
)
echo Using Python: !PY!
"!PY!" scripts\collect_snapshots.py --symbols-file scripts\demo_symbols.txt %*
echo.
echo Finished with exit code !errorlevel!. You can close this window now.
pause
