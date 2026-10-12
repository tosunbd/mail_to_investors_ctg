@echo off
rem  Shared launcher used by the .bat files in the project folder.
rem  Usage: run_job.bat <portfolio|sale-purchase|check> [options] [--auto]
setlocal EnableExtensions
cd /d "%~dp0.."

set "AUTO="
for %%A in (%*) do if /i "%%~A"=="--auto" set "AUTO=1"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo  The program is not installed yet. Double-click setup_windows.bat first.
    echo.
    if not defined AUTO pause
    exit /b 2
)

".venv\Scripts\python.exe" -m icb_mailer %*
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" echo  Finished successfully.
if "%RC%"=="1" echo  Finished, but some emails were NOT sent - see the report in output\reports
if "%RC%"=="2" echo  STOPPED because of an error - see the messages above and logs\icb_mailer.log
if "%RC%"=="3" echo  Another run is still in progress - nothing was done.
echo.
if not defined AUTO pause
exit /b %RC%
