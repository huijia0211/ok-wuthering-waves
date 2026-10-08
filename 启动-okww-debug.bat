@echo off
chcp 65001 >nul
rem ============================================================
rem  ok-ww 从源码启动（Debug 模式）
rem  Debug 模式会输出更详细的日志，并启用调试相关能力，
rem  排查问题（窗口识别不到、任务卡住）时用这个。
rem ============================================================
cd /d "%~dp0"
echo 工作目录: %CD%
echo 模式: Debug
echo.
".venv\Scripts\python.exe" main_debug.py %*
echo.
echo 程序已退出，退出码: %ERRORLEVEL%
pause
