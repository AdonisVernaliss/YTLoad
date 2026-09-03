@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 ytload.py --ui
) else (
  python ytload.py --ui
)
if errorlevel 1 pause
