@echo off
rem  Tests the database and the mail server login. Sends nothing.
setlocal
cd /d "%~dp0"
title ICB - Connection check
call "%~dp0scripts\run_job.bat" check %*
exit /b %ERRORLEVEL%
