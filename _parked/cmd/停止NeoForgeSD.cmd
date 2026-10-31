@echo off
title 停止 NeoForgeSD
set PYTHONUTF8=1
cd /d "%~dp0"
echo 正在结束 NeoForgeSD 控制台与 SD1.5 动作后端...
python ui\stop.py
ping -n 4 127.0.0.1 >nul
