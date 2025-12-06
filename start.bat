@echo off
chcp 65001 >nul
start "MaiBot-主程序" cmd /k "uv run python .\bot.py"
start "MaiBot-Napcat适配器" cmd /k "cd MaiBot-Napcat-Adapter && uv run python .\main.py"