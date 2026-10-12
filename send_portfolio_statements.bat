@echo off
rem ==========================================================================
rem  Double-click to email the Portfolio Statement (HTML + PDF) to every
rem  investor with a registered email address.
rem  Scheduler: send_portfolio_statements.bat --auto   (no "press any key")
rem ==========================================================================
setlocal
cd /d "%~dp0"
title ICB - Sending Portfolio Statements
call "%~dp0scripts\run_job.bat" portfolio %*
exit /b %ERRORLEVEL%
