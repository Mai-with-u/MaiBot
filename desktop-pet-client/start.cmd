@echo off
setlocal
where uv >nul 2>nul
if errorlevel 1 (
    echo Please install uv, then run this launcher again.
    echo https://docs.astral.sh/uv/getting-started/installation/
    pause
    exit /b 1
)
uv run --project "%~dp0." --python 3.12 maibot-desktop-pet %*
if errorlevel 1 pause
