@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONUTF8=1
call "%~dp0_python.bat"
if not defined PY ( echo [ERROR] Python 3 not found. & pause & exit /b 1 )
echo Using Python: !PY!
echo [INFO] Dependency check: on first run (or after a Python change) this may create .venv and install packages (needs network). "dry" mode does NOT skip it.
call "%~dp0_deps.bat"
if errorlevel 1 ( echo. & echo Dependency check failed. Please run scripts\diagnose.bat and send the result. & pause & exit /b 1 )
rem ===================================================================================================
rem Evaluation 2 (three-group number-hallucination comparison). THIS CALLS THE PAID LLM API.
rem Parameters are FIXED on purpose (authorized remaining budget: 6 CNY for all paid requests, Kimi only):
rem   model        kimi-k3.1-pro (any other model => the run is refused before sending anything)
rem   cost cap     5.0 CNY, cumulative for the output folder docs\eval\ablation-kimi (keeps 1 CNY spare for
rem                estimation error). Re-running does NOT add budget; the ledger is never cleared.
rem   max output   8192 tokens per request, 1 company at a time, at most 1 retry per request
rem Usage:  eval_ablation.bat        show the plan, then ask you to type YES before any request is sent
rem         eval_ablation.bat dry    show the plan only; sends no model request and writes no ledger/output folder.
rem                                  NOT fully read-only: the dependency check above may create .venv / install packages.
rem Do NOT delete docs\eval\ablation-kimi\ledger.jsonl (it is the spend record that enforces the cap).
rem ===================================================================================================
set "LLM_PROVIDER=kimi"
set "LLM_MODEL=kimi-k3.1-pro"
set "LLM_MAX_TOKENS=8192"
set "LLM_REASONING_EFFORT=low"
set "EXTRA=--ask"
if /I "%~1"=="dry" set "EXTRA=--dry-run"
if not "%~1"=="" if /I not "%~1"=="dry" echo [NOTE] Argument "%~1" ignored: provider, model and budget are fixed in this script.
echo.
"!PY!" -m finsight.cli eval ablation --provider kimi --require-model kimi-k3.1-pro --budget 5.0 --workers 1 --max-tokens 8192 --retries 1 !EXTRA!
set "RC=!errorlevel!"
echo.
if "!EXTRA!"=="--dry-run" ( echo Dry run finished. No model request was sent and no ledger or output folder was written (the dependency check may have changed .venv). & pause & exit /b !RC! )
if "!RC!"=="0" echo Done. See docs\eval\ablation-kimi\  ^(run_overview.md, ablation_report.md, audit_sheet.csv^)
if "!RC!"=="2" echo Not all companies finished ^(cost cap reached or failures^). See docs\eval\ablation-kimi\run_overview.md. Re-running does not raise the cap.
if "!RC!"=="1" echo [WARN] Nothing was sent, cancelled, or nothing finished. See the messages above.
echo You can close this window.
pause
exit /b !RC!
