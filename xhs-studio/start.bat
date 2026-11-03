@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul || (echo 未找到 Python，请先安装 Python 3：https://www.python.org/downloads/ & pause & exit /b 1)
if not exist .venv (
  echo 首次运行，正在安装依赖（约 1-3 分钟）...
  python -m venv .venv
  .venv\Scripts\pip install -q -r requirements.txt
  .venv\Scripts\python -m playwright install chromium
)
.venv\Scripts\python run.py
pause
