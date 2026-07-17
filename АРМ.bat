rem ARM operator client launcher
@echo off
chcp 65001 >nul
title ARM Client
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel%==0 (
  python bary_de.py
) else (
  py bary_de.py
)

echo.
echo --- Program exited. If there is an error text above, take a photo of it. ---
pause
