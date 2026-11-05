#!/bin/bash
# macOS / Linux 一键启动：首次运行会自动安装依赖（需要已安装 Python 3.10+）
set -e
cd "$(dirname "$0")"
PY=$(command -v python3 || true)
if [ -z "$PY" ]; then
  echo "未找到 python3，请先安装 Python 3：https://www.python.org/downloads/"
  read -n 1 -p "按任意键退出"; exit 1
fi
if [ ! -d .venv ]; then
  echo "首次运行，正在安装依赖（约 1-3 分钟）..."
  "$PY" -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
  .venv/bin/python -m playwright install chromium
fi
.venv/bin/python run.py
