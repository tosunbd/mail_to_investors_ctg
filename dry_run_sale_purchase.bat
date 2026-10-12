@echo off
rem  Builds every Sale/Purchase Confirmation email but sends NOTHING.
rem  Open the .eml files in output\dry_run\ to check them.
setlocal
cd /d "%~dp0"
title ICB - Sale/Purchase Confirmations DRY RUN
call "%~dp0scripts\run_job.bat" sale-purchase --dry-run %*
exit /b %ERRORLEVEL%
