@echo off
title NeoForgeSD 控制台
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
set SD_HOME=E:\sd\sd-old-aki

echo ==========================================
echo   NeoForgeSD 一键入口
echo   动作后端 :7860   控制台 :8899
echo ==========================================
echo.

python -c "import urllib.request as u;u.urlopen('http://127.0.0.1:7860/sdapi/v1/progress',timeout=3)" 2>nul
if %errorlevel%==0 goto backok
echo [1/3] 启动 SD1.5 动作后端（约 60-120 秒，首次加载模型稍慢）...
start "NeoForgeSD-backend" /min cmd /c "cd /d %SD_HOME% && set HF_HUB_OFFLINE=1&& set TRANSFORMERS_OFFLINE=1&& python\python.exe webui.py --api --port 7860 --medvram --opt-sdp-attention --skip-version-check --no-half-vae"
goto console
:backok
echo [1/3] 后端已在线，跳过启动

:console
echo [2/3] 启动控制台 :8899 ...
start "NeoForgeSD-console" /min cmd /c "cd /d %~dp0 && python ui\console.py"
echo [3/3] 打开浏览器...
ping -n 4 127.0.0.1 >nul
start "" http://127.0.0.1:8899/
echo.
echo 已启动。要停止请运行 停止NeoForgeSD.cmd
ping -n 6 127.0.0.1 >nul
