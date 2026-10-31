@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONUTF8=1
call "%~dp0_python.bat"
if not defined PY ( echo [ERROR] Python 3 not found. & pause & exit /b 1 )
echo Using Python: !PY!
call "%~dp0_deps.bat"
if errorlevel 1 ( echo. & echo Dependency check failed. Please run scripts\diagnose.bat and send the result. & pause & exit /b 1 )
rem Evaluation 2: three-group number-hallucination comparison (calls the paid LLM API).
rem Usage: eval_ablation.bat [provider] [budget_cny]     defaults: kimi 9
rem Resumable: companies already finished are skipped, and the budget is cumulative per output folder.
set "PROVIDER=%~1"
if "!PROVIDER!"=="" set "PROVIDER=kimi"
set "BUDGET=%~2"
if "!BUDGET!"=="" set "BUDGET=9"
echo.
echo Evaluation 2 - provider: !PROVIDER!   cost cap: !BUDGET! CNY (estimated, cumulative for the output folder)
echo 9 companies, about 2-4 minutes each, running 2 at a time. Safe to close and run again later to resume.
echo.
"!PY!" -m finsight.cli eval ablation --provider !PROVIDER! --budget !BUDGET! --workers 2
set "RC=!errorlevel!"
echo.
if "!RC!"=="0" echo Done. Results: docs\eval\ablation-!PROVIDER!\  ^(ablation_report.md, audit_sheet.csv^)
if "!RC!"=="2" echo Not all companies finished. Run this script again to continue the missing ones.
if "!RC!"=="1" echo [WARN] Nothing was run or nothing finished. See the messages above.
echo You can close this window.
pause
exit /b !RC!
