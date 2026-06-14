@echo off
chcp 65001 >nul
echo Starting Obscura Web UI...
python -m obscura_manager.server
pause
