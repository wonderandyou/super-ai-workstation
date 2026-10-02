@echo off
rem ============================================================================
rem  dsh web launcher (standalone / manual copy version)
rem
rem  The DSH installer injects its own launcher into the installed package:
rem      %APPDATA%\npm\node_modules\@deepseek-ai\dsh\dsh-web.cmd
rem      %APPDATA%\npm\dsh-web.cmd
rem  This copy is for manual use when you just want to start the Web UI
rem  without the baked-in absolute path (it looks dsh.cmd up in PATH).
rem
rem  Usage:  dsh-web.cmd                 -> dsh web --port 3080
rem          dsh-web.cmd --port 8080     -> custom port
rem          dsh-web.cmd --no-open       -> do not open the browser
rem ============================================================================
chcp 65001 >nul
setlocal EnableExtensions
title DSH Web  -  DeepSeek Harness

set "DSH_CMD="
for /f "delims=" %%I in ('where dsh.cmd 2^>nul') do if not defined DSH_CMD set "DSH_CMD=%%I"
if not defined DSH_CMD if exist "%APPDATA%\npm\dsh.cmd" set "DSH_CMD=%APPDATA%\npm\dsh.cmd"

if not defined DSH_CMD (
  echo.
  echo [ERROR] dsh.cmd not found. Install DSH first ^(run Install-DSH.cmd^).
  echo.
  pause
  exit /b 1
)

set "DSH_PORT=3080"
if "%~1"=="" ( set "DSH_ARGS=web --port %DSH_PORT%" ) else ( set "DSH_ARGS=%*" )

echo ============================================================
echo   DeepSeek Harness - Web UI
echo   command : "%DSH_CMD%" %DSH_ARGS%
echo   The browser opens automatically with an authorized URL.
echo   Keep this window open while using DSH. Ctrl+C stops it.
echo ============================================================
echo.

call "%DSH_CMD%" %DSH_ARGS%
set "DSH_EXIT=%ERRORLEVEL%"

echo.
if not "%DSH_EXIT%"=="0" (
  echo [ERROR] dsh web exited with code %DSH_EXIT%.
  echo         - API key / balance problems: see platform.deepseek.com
  echo         - missing frontend files: reinstall DSH
) else (
  echo dsh web stopped.
)
echo.
pause
exit /b %DSH_EXIT%
