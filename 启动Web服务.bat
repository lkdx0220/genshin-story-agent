@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ================================================
echo     YuanShen Story Advisor - Web Service
echo ================================================
echo.

start "" python genshin_story_web_api.py
timeout /t 3 /nobreak >nul

echo.
echo Service started!
echo   API:  http://localhost:5000
echo   Web:  http://localhost:5000
echo.

pause
