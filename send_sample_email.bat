@echo off
rem  Emails ONE sample Portfolio Statement (built-in SAMPLE data, no database,
rem  no investor) to the test address in settings.toml (test_recipient).
rem  Other address:  send_sample_email.bat --to someone@example.com
setlocal
cd /d "%~dp0"
title ICB - Send sample email
call "%~dp0scripts\run_job.bat" sample %*
exit /b %ERRORLEVEL%
