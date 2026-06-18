@echo off
chcp 65001 >nul
echo ========================================
echo   EngramNote 一键启动脚本
echo ========================================
echo.

REM 检查 mineru_env 环境
set PYTHON=C:\Users\admin\anaconda3\envs\mineru_env\python.exe
if not exist "%PYTHON%" (
    echo [错误] 未找到 mineru_env Python: %PYTHON%
    echo 请确认 Anaconda 已安装且 mineru_env 环境存在
    pause
    exit /b 1
)

echo [✓] Python 环境: %PYTHON%
%PYTHON% --version

REM 检查 Node.js
where node >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Node.js，请安装 Node.js 18+
    pause
    exit /b 1
)
echo [✓] Node.js 环境:
node --version

echo.
echo 正在启动各服务...
echo.

REM 启动后端 uvicorn
echo [1/3] 启动后端服务 (uvicorn)...
start "EngramNote-Backend" cmd /k "cd /d %~dp0backend && %PYTHON% -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload-dir app"

REM 等待后端启动
timeout /t 3 /nobreak >nul

REM 启动 Celery worker
echo [2/3] 启动 Celery worker...
start "EngramNote-Celery" cmd /k "cd /d %~dp0backend && %PYTHON% -m celery -A app.tasks.celery_app worker --loglevel=info --pool=solo -Q celery"

REM 启动前端开发服务器
echo [3/3] 启动前端开发服务器 (Vite)...
start "EngramNote-Frontend" cmd /k "cd /d %~dp0frontend && npm run dev"

echo.
echo ========================================
echo   所有服务已启动！
echo   后端: http://localhost:8000
echo   前端: http://localhost:5173
echo   API 文档: http://localhost:8000/docs
echo ========================================
echo.
echo 按任意键退出此窗口（服务将继续运行）...
pause >nul
