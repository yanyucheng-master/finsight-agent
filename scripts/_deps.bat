@echo off
rem Ensure a project virtualenv (.venv) with backend dependencies; sets PY to the venv python.
rem Never installs into the global Python. Log: data\runs\pip_install.log
if not exist data\runs mkdir data\runs
if exist ".venv\Scripts\python.exe" goto have_venv
if not defined BASEPY set "BASEPY=%PY%"
"%BASEPY%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
  echo [ERROR] Python 3.10+ is required. Found:
  "%BASEPY%" --version
  exit /b 2
)
echo Creating project virtualenv .venv (first run only)...
"%BASEPY%" -m venv .venv > data\runs\pip_install.log 2>&1
if errorlevel 1 (
  echo [ERROR] Could not create .venv. See data\runs\pip_install.log
  exit /b 3
)
:have_venv
set "PY=%CD%\.venv\Scripts\python.exe"
"%PY%" -c "import fastapi, httpx, uvicorn, dotenv, pydantic; assert int(pydantic.VERSION.split('.')[0]) >= 2" >nul 2>nul
if not errorlevel 1 exit /b 0
echo Installing backend dependencies into .venv (first run only, 1-3 minutes)...
"%PY%" -m pip install -e backend >> data\runs\pip_install.log 2>&1
if not errorlevel 1 exit /b 0
echo Default PyPI failed, retrying with Tsinghua mirror...
"%PY%" -m pip install -e backend -i https://pypi.tuna.tsinghua.edu.cn/simple >> data\runs\pip_install.log 2>&1
if not errorlevel 1 exit /b 0
echo [ERROR] Dependency installation failed. See data\runs\pip_install.log
exit /b 3
