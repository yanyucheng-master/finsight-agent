@echo off
rem Ensure Python >= 3.10 and backend dependencies. Requires PY (from _python.bat). Logs to data\runs\pip_install.log
if not exist data\runs mkdir data\runs
"%PY%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
  echo [ERROR] Python 3.10+ is required. Found:
  "%PY%" --version
  echo Fix: open "Anaconda Prompt" and run:  conda create -n finsight python=3.11 -y
  exit /b 2
)
"%PY%" -c "import fastapi, httpx, uvicorn, dotenv, pydantic; assert int(pydantic.VERSION.split('.')[0]) >= 2" >nul 2>nul
if not errorlevel 1 exit /b 0
echo Installing backend dependencies (first run only)...
"%PY%" -m pip install -e backend > data\runs\pip_install.log 2>&1
if not errorlevel 1 exit /b 0
echo Default PyPI failed, retrying with Tsinghua mirror...
"%PY%" -m pip install -e backend -i https://pypi.tuna.tsinghua.edu.cn/simple >> data\runs\pip_install.log 2>&1
if not errorlevel 1 exit /b 0
echo [ERROR] Dependency installation failed. See data\runs\pip_install.log
exit /b 3
