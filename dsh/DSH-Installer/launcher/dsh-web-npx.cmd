@echo off
rem ============================================================================
rem  dsh web launcher - official "npx" form (no global install required)
rem
rem  Official quickstart (DeepSeek Harness docs):
rem      https://deepseek-harness.github.io/deepseek-harness/guide/quickstart
rem      npx @deepseek-ai/dsh web
rem
rem  Use this when the global "dsh" command is missing, broken, or you simply
rem  do not want a global install. npx downloads the official package into its
rem  cache and runs it - same official package, same Web UI.
rem
rem  Usage:  dsh-web-npx.cmd                -> dsh web --port 3080
rem          dsh-web-npx.cmd --port 8080    -> custom port
rem          dsh-web-npx.cmd --no-open      -> do not open the browser
rem ============================================================================
chcp 65001 >nul
setlocal EnableExtensions
title DSH Web (npx)  -  DeepSeek Harness

where npx.cmd >nul 2>nul
if errorlevel 1 (
  echo.
  echo [ERROR] npx.cmd not found. Install Node.js first ^(run Install-DSH.cmd^).
  echo.
  pause
  exit /b 1
)

set "DSH_PORT=3080"
if "%~1"=="" ( set "DSH_ARGS=web --port %DSH_PORT%" ) else ( set "DSH_ARGS=%*" )

echo ============================================================
echo   DeepSeek Harness - Web UI  ^(npx mode^)
echo   command : npx -y @deepseek-ai/dsh %DSH_ARGS%
echo   The browser opens automatically with an authorized URL.
echo   Keep this window open while using DSH. Ctrl+C stops it.
echo ============================================================
echo.

call npx -y @deepseek-ai/dsh %DSH_ARGS%
set "DSH_EXIT=%ERRORLEVEL%"

echo.
if not "%DSH_EXIT%"=="0" (
  echo [ERROR] npx launcher exited with code %DSH_EXIT%.
  echo         - API key / balance problems: see platform.deepseek.com
  echo         - no network to registry.npmjs.org: try a mirror or a proxy
) else (
  echo dsh web stopped.
)
echo.
pause
exit /b %DSH_EXIT%
