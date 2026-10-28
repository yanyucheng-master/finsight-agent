@echo off
rem 查找可用的 Python 3（供其他 .bat 调用，结果写入变量 PY）
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
