@echo off
chcp 65001 >nul
rem ============================================================
rem  ok-ww 从源码启动（正常模式）
rem  必须以仓库根目录作为工作目录，否则 configs/logs/cache
rem  会被创建到错误的位置。
rem ============================================================
cd /d "%~dp0"
echo 工作目录: %CD%
echo 使用解释器: %CD%\.venv\Scripts\python.exe
echo.
".venv\Scripts\python.exe" main.py %*
set EXITCODE=%ERRORLEVEL%
echo.
echo 程序已退出，退出码: %EXITCODE%
pause
