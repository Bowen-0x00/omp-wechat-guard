@echo off
chcp 65001 >nul
python "%~dp0sync_to_aliyun.py"
pause
