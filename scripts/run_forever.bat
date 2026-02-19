@echo off
title GifMake Watchdog
cd /d "%~dp0\.."

:loop
echo [%date% %time%] Starting GifMake...
python src/main.py
echo [%date% %time%] GifMake exited (code %errorlevel%). Restarting in 10s...
timeout /t 10 /nobreak >nul
goto loop
