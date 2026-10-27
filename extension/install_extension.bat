@echo off
chcp 65001 >nul
echo ========================================================
echo 正在安装 omp 企业微信守护扩展至用户目录...
echo ========================================================

set TARGET_DIR=%USERPROFILE%\.omp\agent\extensions
if not exist "%TARGET_DIR%" (
    mkdir "%TARGET_DIR%"
)

copy /y "%~dp0omp-wechat-guard.ts" "%TARGET_DIR%\omp-wechat-guard.ts"

if %errorlevel% equ 0 (
    echo [成功] 扩展已安装至: %TARGET_DIR%\omp-wechat-guard.ts
    echo 下次启动或重启 omp 交互会话即可自动加载！
) else (
    echo [失败] 扩展安装失败，请检查目录权限。
)
pause
