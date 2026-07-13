@echo off
setlocal enabledelayedexpansion
title EngramNote

:: ============================================================
::  EngramNote One-Click Start Script (Windows)
::  自动检测脚本目录，无需手动配置路径
:: ============================================================

:: ---- 自动获取项目目录（脚本所在目录） ----
set "PROJECT_DIR=%~dp0"
set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"
set "BACKEND_DIR=%PROJECT_DIR%\backend"
set "FRONTEND_DIR=%PROJECT_DIR%\frontend"
set "BACKEND_PORT=8000"
set "FRONTEND_PORT=5173"

:: ---- Conda 环境配置（按需修改） ----
:: 如果使用 conda，请设置 CONDA_BASE 为你的 conda 安装路径
:: 例如：set "CONDA_BASE=C:\Users\你的用户名\anaconda3"
:: 如果不使用 conda，留空即可（使用系统 Python）
set "CONDA_BASE="
set "CONDA_ENV=mineru_env"

echo.
echo ============================================================
echo   EngramNote - One Click Start
echo ============================================================
echo   Project: %PROJECT_DIR%
echo.

:: ---- 0. 运行环境检测 ----
echo [0/5] Checking environment...
cd /d "%PROJECT_DIR%"
python check_env.py --check
if errorlevel 1 (
    echo.
    echo [WARN] 环境检测发现问题，建议运行：python check_env.py --fix
    echo        是否继续启动？(Y/N)
    set /p continue=
    if /i "!continue!" neq "Y" (
        echo [INFO] 启动已取消。请先修复环境问题。
        pause
        exit /b 1
    )
)
echo [OK] Environment check passed
echo.

:: ---- 1. 激活 Conda 环境（如配置） ----
echo [1/5] Preparing Python environment...
if defined CONDA_BASE (
    if exist "%CONDA_BASE%\condabin\conda.bat" (
        call "%CONDA_BASE%\condabin\conda.bat" activate %CONDA_ENV%
        if errorlevel 1 (
            echo [ERROR] Cannot activate conda env [%CONDA_ENV%]
            echo         Please verify: conda env list
            echo         Or edit CONDA_BASE at the top of this script.
            pause
            exit /b 1
        )
        echo [OK] Conda env [%CONDA_ENV%] activated
    ) else (
        echo [WARN] CONDA_BASE path not found: %CONDA_BASE%
        echo        Using system Python instead.
    )
) else (
    echo [OK] Using system Python
)

:: ---- 2. 检查后端依赖 ----
echo.
echo [2/5] Checking backend dependencies...
cd /d "%BACKEND_DIR%"
python -c "import fastapi" 2>nul
if errorlevel 1 (
    echo [INFO] Installing backend dependencies (tsinghua mirror)...
    pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
    if errorlevel 1 (
        echo [ERROR] Backend dependency install failed
        pause
        exit /b 1
    )
) else (
    echo [OK] Backend dependencies ready
)

:: ---- 3. 检查前端依赖 ----
echo.
echo [3/5] Checking frontend dependencies...
cd /d "%FRONTEND_DIR%"
if not exist "node_modules" (
    echo [INFO] Installing frontend dependencies (taobao mirror)...
    npm install --registry=https://registry.npmmirror.com
    if errorlevel 1 (
        echo [ERROR] Frontend dependency install failed
        pause
        exit /b 1
    )
) else (
    echo [OK] Frontend dependencies ready
)

:: ---- 4. 创建数据目录 ----
echo.
echo [4/5] Checking data directories...
if not exist "%BACKEND_DIR%\data\db" mkdir "%BACKEND_DIR%\data\db"
if not exist "%BACKEND_DIR%\data\storage" mkdir "%BACKEND_DIR%\data\storage"
if not exist "%BACKEND_DIR%\data\celery\broker" mkdir "%BACKEND_DIR%\data\celery\broker"
if not exist "%BACKEND_DIR%\data\celery\results" mkdir "%BACKEND_DIR%\data\celery\results"
if not exist "%BACKEND_DIR%\data\chroma" mkdir "%BACKEND_DIR%\data\chroma"
if not exist "%BACKEND_DIR%\data\logs" mkdir "%BACKEND_DIR%\data\logs"
if not exist "%BACKEND_DIR%\data\models\silero-vad" mkdir "%BACKEND_DIR%\data\models\silero-vad"
echo [OK] Data directories ready

:: ---- 5. 启动服务 ----
echo.
echo [5/5] Starting services...
echo.

echo [START] Backend API (port %BACKEND_PORT%)...
start "EngramNote-API" cmd /k "cd /d %BACKEND_DIR% && python -m uvicorn app.main:app --reload --port %BACKEND_PORT% --reload-dir app"

echo Waiting 3s for backend to initialize...
timeout /t 3 /nobreak >nul

echo [START] Celery Worker...
start "EngramNote-Celery" cmd /k "cd /d %BACKEND_DIR% && python -m celery -A app.tasks.celery_app:celery_app worker --loglevel=info --pool=solo"

echo [START] Frontend dev server (port %FRONTEND_PORT%)...
start "EngramNote-Frontend" cmd /k "cd /d %FRONTEND_DIR% && npm run dev"

:: ---- Done ----
echo.
echo ============================================================
echo   All services started!
echo ============================================================
echo.
echo   Backend API:  http://localhost:%BACKEND_PORT%
echo   API Docs:      http://localhost:%BACKEND_PORT%/docs
echo   Frontend:      http://localhost:%FRONTEND_PORT%
echo.
echo   3 windows opened. Close a window to stop its service.
echo.
echo   Note: First file upload may take ~30s (loading embedding model).
echo.
pause
