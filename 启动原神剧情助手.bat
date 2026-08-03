@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo               YuanShen Story Advisor Agent
echo ============================================================
echo.
echo   LLM: qwen3.7-max
echo   Arch: 别名归一化 - Agent循环 (LangGraph) / 双轨制长任务加载
echo.
echo ============================================================
echo   Type 'help' for usage, 'quit' to exit
echo ============================================================
echo.

python genshin_story_agent.py
pause
