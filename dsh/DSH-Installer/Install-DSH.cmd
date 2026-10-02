@echo off
setlocal EnableExtensions
title DSH Installer
rem ============================================================================
rem  DSH (DeepSeek Harness) one-click installer - bootstrap
rem  Keep this file PURE ASCII: cmd.exe mis-parses multi-byte text.
rem ============================================================================

set "HERE=%~dp0"
set "PS1=%HERE%Install-DSH.ps1"
set "BOOTLOG=%TEMP%\DSH-install-boot.log"
set "PSLOG=%TEMP%\DSH-install-output.log"

>>"%BOOTLOG%" echo ================================================================
>>"%BOOTLOG%" echo [%date% %time%] bootstrap start
>>"%BOOTLOG%" echo HERE=%HERE%
>>"%BOOTLOG%" echo PS1=%PS1%
>>"%BOOTLOG%" echo TEMP=%TEMP%
>>"%BOOTLOG%" echo OS=%OS%  ARCH=%PROCESSOR_ARCHITECTURE%

cls
echo.
echo  ================================================================
echo    DSH Installer  -  bootstrap
echo  ================================================================
echo    Folder : %HERE%
echo    Trace  : %BOOTLOG%
echo.

if not exist "%PS1%" goto NO_PS1

set "PSEXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PSEXE%" set "PSEXE=powershell.exe"

set "INZIP="
if not "%HERE%"=="%HERE:.zip\=%" set "INZIP=1"
if defined INZIP goto IN_ZIP

echo  [1/5] checking PowerShell ...
"%PSEXE%" -NoProfile -Command "exit 0" >nul 2>&1
if not "%ERRORLEVEL%"=="0" goto NO_PS

echo  [2/5] collecting environment info ...
>>"%BOOTLOG%" echo --- powershell ---
"%PSEXE%" -NoProfile -Command "$PSVersionTable.PSVersion.ToString()" >>"%BOOTLOG%" 2>&1
>>"%BOOTLOG%" echo --- ansicode page ---
"%PSEXE%" -NoProfile -Command "[System.Text.Encoding]::Default.WebName" >>"%BOOTLOG%" 2>&1
>>"%BOOTLOG%" echo --- antivirus ---
"%PSEXE%" -NoProfile -Command "Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct -ErrorAction SilentlyContinue | Select-Object -ExpandProperty displayName" >>"%BOOTLOG%" 2>&1

echo  [3/5] clearing the downloaded-from-internet lock ...
"%PSEXE%" -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath '%~dp0' -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue" >nul 2>&1

echo  [4/5] making sure the script is UTF-8 with BOM ...
"%PSEXE%" -NoProfile -ExecutionPolicy Bypass -Command "$p='%PS1%'; try { $b=[IO.File]::ReadAllBytes($p); if ($b.Length -ge 3 -and $b[0] -eq 0xEF -and $b[1] -eq 0xBB -and $b[2] -eq 0xBF) { Write-Host 'BOM ok' } else { [IO.File]::WriteAllBytes($p, ([byte[]](0xEF,0xBB,0xBF)+$b)); Write-Host 'BOM was missing - fixed it' } } catch { Write-Host ('BOM check failed: ' + $_.Exception.Message) }" >>"%BOOTLOG%" 2>&1
type "%BOOTLOG%" | findstr /c:"BOM" >nul 2>&1

echo  [5/5] starting the installer, please wait ...
echo.
"%PSEXE%" -NoProfile -STA -ExecutionPolicy Bypass -File "%PS1%" %* >"%PSLOG%" 2>&1
set "CODE=%ERRORLEVEL%"
>>"%BOOTLOG%" echo [%date% %time%] ps1 exit=%CODE%

echo.
if not "%CODE%"=="0" goto FAILED
echo  Installer finished, exit code 0.
echo  Log: %PSLOG%
echo.
echo  Closing in 8 seconds ...
timeout /t 8 >nul
exit /b 0

:FAILED
echo  [FAILED] exit code %CODE%
echo.
echo  -------------------- error output --------------------
type "%PSLOG%"
echo  -------------------- end --------------------
echo.
echo  Also see: %BOOTLOG%
echo            %LOCALAPPDATA%\DSH-Installer\install-*.log
echo.
goto HOLD

:NO_PS1
echo  [ERROR] Install-DSH.ps1 was not found next to this file.
echo          This folder : %HERE%
echo          Extract the WHOLE folder from the zip.
echo.
goto HOLD

:IN_ZIP
echo  [ERROR] You are running inside the ZIP temp folder.
echo          RIGHT-CLICK the zip, choose "Extract All", then run this
echo          file from the extracted folder.
echo          Current path: %HERE%
echo.
goto HOLD

:NO_PS
echo  [ERROR] PowerShell cannot be started - usually antivirus.
echo          Command tried: %PSEXE%
echo.
goto HOLD

:HOLD
echo  ================================================================
echo   This window is kept open on purpose.
echo   Please send these files to the developer:
echo     %BOOTLOG%
echo     %PSLOG%
echo  ================================================================
echo.
pause
exit /b 1
