@echo off
rem ==========================================================================
rem  Double-click to email today's Sale/Purchase Confirmations to investors.
rem  Scheduler: send_sale_purchase_confirmations.bat --auto
rem ==========================================================================
setlocal
cd /d "%~dp0"
title ICB - Sending Sale/Purchase Confirmations
call "%~dp0scripts\run_job.bat" sale-purchase %*
exit /b %ERRORLEVEL%
