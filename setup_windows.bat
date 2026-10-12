@echo off
rem ==========================================================================
rem  One-time setup on a Windows PC (safe to run again at any time).
rem   1. finds 32-bit Python 3.12 (installs it from installers\ if missing)
rem   2. creates the private environment .venv
rem   3. installs the libraries (offline from wheels\ when available)
rem   4. tests the database and mail server connections
rem ==========================================================================
setlocal EnableExtensions
cd /d "%~dp0"
title ICB investor mailer - setup

rem  A fresh copy from GitHub has only the templates: create the private files.
if not exist "settings.toml" (
    copy /y "settings.example.toml" "settings.toml" >nul
    echo  Created settings.toml from settings.example.toml - fill in the database address.
)
if not exist ".env" (
    copy /y ".env.example" ".env" >nul
    echo  Created .env from .env.example - fill in the user names and passwords.
)

echo.
echo  [1/4] Looking for 32-bit Python 3.12 ...
set "PY="
call :find_python
if not defined PY (
    if exist "installers\python-3.12.10.exe" (
        echo        Not found - installing Python 3.12 32-bit for the current user ...
        "installers\python-3.12.10.exe" /passive InstallAllUsers=0 Include_launcher=1 InstallLauncherAllUsers=0 PrependPath=0 Include_test=0
        call :find_python
    )
)
if not defined PY (
    echo.
    echo  ERROR: 32-bit Python 3.12 was not found.
    echo  Install "Windows installer (32-bit)" of Python 3.12 from python.org
    echo  ^(or run installers\python-3.12.10.exe^) and run this setup again.
    echo  32-bit is needed because the Oracle client for the 10g database is 32-bit.
    goto :fail
)
echo        Using: %PY%

echo.
echo  [2/4] Creating the environment .venv ...
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import struct,sys; sys.exit(0 if struct.calcsize('P')==4 and sys.version_info[:2]==(3,12) else 1)"
    if errorlevel 1 (
        echo        Existing .venv is not 32-bit Python 3.12 - recreating it.
        rmdir /s /q ".venv"
    )
)
if not exist ".venv\Scripts\python.exe" (
    %PY% -m venv .venv
    if errorlevel 1 goto :fail
)

echo.
echo  [3/4] Installing libraries ...
if exist "wheels\*.whl" (
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --no-index --find-links wheels -r requirements.txt
) else (
    echo        wheels\ folder not found - downloading the libraries from the internet ...
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
)
if errorlevel 1 goto :fail

echo.
echo  [4/4] Testing the database and mail server ...
".venv\Scripts\python.exe" -m icb_mailer check
if errorlevel 1 (
    echo.
    echo  Setup finished, but the connection check FAILED - see the messages above.
    echo  Fix settings.toml / .env and run check_connections.bat.
    pause
    exit /b 1
)

echo.
echo  Setup complete. Use send_portfolio_statements.bat to send.
pause
exit /b 0

:find_python
for %%V in ("-V:3.12-32" "-3.12-32") do (
    if not defined PY (
        py %%~V -c "import struct,sys; sys.exit(0 if struct.calcsize('P')==4 else 1)" >nul 2>&1 && set "PY=py %%~V"
    )
)
rem  Right after installing, the py launcher may not be on PATH in this window yet.
for %%P in ("%LOCALAPPDATA%\Programs\Python\Python312-32\python.exe" "%ProgramFiles(x86)%\Python312-32\python.exe" "C:\Python312-32\python.exe") do (
    if not defined PY if exist "%%~P" set "PY="%%~P""
)
exit /b 0

:fail
echo.
echo  SETUP FAILED.
pause
exit /b 2
