@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
rem Push the project history (from .repo\finsight-agent.bundle) to GitHub using YOUR git login.
rem Plain push only: never forces. If the remote already has other history it will refuse; tell Claude.
cd /d "%~dp0.."
set "URL=https://github.com/yanyucheng-master/finsight-agent.git"
set "BUNDLE=%CD%\.repo\finsight-agent.bundle"
set "WORK=%TEMP%\finsight-push"

where git >nul 2>nul
if errorlevel 1 ( echo [ERROR] git not found. Install Git for Windows first: https://git-scm.com/download/win & pause & exit /b 1 )
if not exist "%BUNDLE%" ( echo [ERROR] Bundle not found: %BUNDLE% & pause & exit /b 1 )

if exist "%WORK%" rmdir /s /q "%WORK%"
git clone "%BUNDLE%" "%WORK%"
if errorlevel 1 ( echo [ERROR] Cannot read the bundle. & pause & exit /b 1 )
cd /d "%WORK%"
git remote set-url origin %URL%

echo.
echo Remote branches before push (empty means the GitHub repo has no commits yet):
git ls-remote --heads origin
echo.
echo Pushing to %URL% ... (a browser or credential window may ask you to sign in to GitHub)
git push -u origin master
if errorlevel 1 (
  echo.
  echo [FAILED] Push was refused or sign-in failed. Do NOT force-push. Copy the message above to Claude.
  pause
  exit /b 1
)
echo.
echo [OK] Pushed. Tell Claude "pushed".
pause
