@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONUTF8=1
call "%~dp0_python.bat"
if not defined PY ( echo [ERROR] Python 3 not found. & pause & exit /b 1 )
echo Using Python: !PY!
"!PY!" -c "import finsight, fastapi, httpx" 2>nul || "!PY!" -m pip install -e backend
if not exist data\runs mkdir data\runs
echo.
echo === 1/2 LLM connectivity ===
"!PY!" -m finsight.cli llm-check --provider deepseek
"!PY!" -m finsight.cli llm-check --provider kimi
echo.
echo === 2/2 Verified research briefs (DeepSeek) ===
for %%C in (600519 000002 600518) do (
  echo   - %%C ...
  "!PY!" -m finsight.cli report %%C --mode llm --provider deepseek --out data\runs\report_%%C_deepseek.json > data\runs\report_%%C_deepseek.log 2>&1
)
echo.
echo Done. Results are in data\runs\ . You can close this window.
pause
