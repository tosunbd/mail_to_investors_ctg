@echo off
rem  Builds every Portfolio Statement email but sends NOTHING.
rem  Open the .eml files in output\dry_run\ (Outlook/Thunderbird) to check them,
rem  and the PDFs in output\pdf\.
setlocal
cd /d "%~dp0"
title ICB - Portfolio Statements DRY RUN
call "%~dp0scripts\run_job.bat" portfolio --dry-run %*
exit /b %ERRORLEVEL%
