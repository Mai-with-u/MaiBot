@echo off
setlocal
where npm.cmd >nul 2>nul
if errorlevel 1 (
    echo Please install Node.js and npm, then run this launcher again.
    pause
    exit /b 1
)
pushd "%~dp0"
if errorlevel 1 exit /b 1
if not exist "node_modules\.bin\tauri.cmd" (
    echo Client dependencies are missing. Run npm ci in this directory first.
    popd
    pause
    exit /b 1
)
call npm.cmd start -- %*
set "petExitCode=%errorlevel%"
popd
if not "%petExitCode%"=="0" pause
exit /b %petExitCode%
