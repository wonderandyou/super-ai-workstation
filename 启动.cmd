@echo off
rem 超级AI工作台 · 启动（兜底用；优先双击 启动.vbs）
rem 有问题加Q:3153180025
cd /d "%~dp0"
if not exist "runtime\pythonw.exe" (
  echo 找不到自带的 Python 运行时 runtime\pythonw.exe
  echo 请把压缩包完整解压出来再运行。
  pause
  exit /b 1
)
start "" "%~dp0runtime\pythonw.exe" "%~dp0app.py"
