@echo off
rem Find Python. Prefer the project virtualenv (.venv, created by _deps.bat); result in variable PY
set "PY="
set "BASEPY="
if exist "%~dp0..\.venv\Scripts\python.exe" set "PY=%~dp0..\.venv\Scripts\python.exe"
for %%P in (
  "%USERPROFILE%\anaconda3\python.exe"
  "%USERPROFILE%\miniconda3\python.exe"
  "%LOCALAPPDATA%\anaconda3\python.exe"
  "%LOCALAPPDATA%\miniconda3\python.exe"
  "%ProgramData%\anaconda3\python.exe"
  "%ProgramData%\miniconda3\python.exe"
) do (
  if not defined BASEPY if exist %%P set "BASEPY=%%~P"
)
if not defined BASEPY (
  for /f "delims=" %%I in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "BASEPY=%%I"
)
if not defined BASEPY (
  for /f "delims=" %%I in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "BASEPY=%%I"
)
if not defined PY set "PY=%BASEPY%"
