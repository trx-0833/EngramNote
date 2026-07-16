@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion
title EngramNote

:: ============================================================
::  EngramNote One-Click Start Script (Windows)
::  Auto-detect script directory, no manual path configuration needed
:: ============================================================

:: ---- Auto-detect project directory (where script is located) ----
set "PROJECT_DIR=%~dp0"
set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"
set "BACKEND_DIR=%PROJECT_DIR%\backend"
set "FRONTEND_DIR=%PROJECT_DIR%\frontend"
set "BACKEND_PORT=8000"
set "FRONTEND_PORT=5173"

:: ---- Conda environment config (auto-detect, no manual config needed) ----
set "CONDA_ENV=mineru_env"
set "CONDA_BASE="

:: Auto-detect conda installation path (common locations)
if exist "%USERPROFILE%\anaconda3\condabin\conda.bat" (
    set "CONDA_BASE=%USERPROFILE%\anaconda3"
) else if exist "%USERPROFILE%\miniconda3\condabin\conda.bat" (
    set "CONDA_BASE=%USERPROFILE%\miniconda3"
) else if exist "C:\ProgramData\anaconda3\condabin\conda.bat" (
    set "CONDA_BASE=C:\ProgramData\anaconda3"
)
:: If auto-detect fails, uncomment and modify the next line:
:: set "CONDA_BASE=C:\Users\YOUR_USERNAME\anaconda3"

echo.
echo ============================================================
echo   EngramNote - One Click Start
echo ============================================================
echo   Project: %PROJECT_DIR%
echo.

:: ---- Step 1: Activate Conda environment (must run before check_env.py) ----
echo [1/7] Preparing Python environment...
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
    echo [WARN] Conda not detected, using system Python
    echo        To use conda, edit CONDA_BASE at the top of this script.
)

:: ---- Step 2: Detect Node.js/npm path (auto-detect nvm-windows) ----
echo.
echo [2/7] Checking Node.js/npm path...
set "NVM_SYMLINK_PATH="

:: Check if npm is already in PATH
where npm >nul 2>nul
if errorlevel 1 (
    :: npm not in PATH, try common nvm-windows locations
    if exist "C:\nvm4w\nodejs\npm.cmd" (
        set "NVM_SYMLINK_PATH=C:\nvm4w\nodejs"
    ) else if exist "%NVM_SYMLINK%\npm.cmd" (
        set "NVM_SYMLINK_PATH=%NVM_SYMLINK%"
    ) else if exist "%USERPROFILE%\AppData\Local\nvm\nodejs\npm.cmd" (
        set "NVM_SYMLINK_PATH=%USERPROFILE%\AppData\Local\nvm\nodejs"
    )

    if defined NVM_SYMLINK_PATH (
        set "PATH=%NVM_SYMLINK_PATH%;%PATH%"
        echo [OK] Node.js path added to PATH: %NVM_SYMLINK_PATH%
    ) else (
        echo [WARN] npm not detected. Please ensure Node.js 18+ is installed.
        echo        Download: https://nodejs.org/
    )
) else (
    echo [OK] npm is already in PATH
)

:: ---- Step 3: Run environment check (using activated Python environment) ----
echo.
echo [3/7] Checking environment...
cd /d "%PROJECT_DIR%"
set PYTHONIOENCODING=utf-8
python check_env.py --check
if errorlevel 1 (
    echo.
    echo [WARN] Environment check found issues. Run: python check_env.py --fix
    echo        Continue startup? ^(Y/N^)
    set /p continue=
    if /i "!continue!" neq "Y" (
        echo [INFO] Startup cancelled. Please fix environment issues first.
        pause
        exit /b 1
    )
)
echo [OK] Environment check passed

:: ---- Step 4: Check backend dependencies ----
echo.
echo [4/7] Checking backend dependencies...
cd /d "%BACKEND_DIR%"
python -c "import fastapi" 2>nul
if errorlevel 1 (
    echo [INFO] Installing backend dependencies ^(tsinghua mirror^)...
    pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
    if errorlevel 1 (
        echo [ERROR] Backend dependency install failed
        pause
        exit /b 1
    )
) else (
    echo [OK] Backend dependencies ready
)

:: ---- Step 5: Check frontend dependencies ----
echo.
echo [5/7] Checking frontend dependencies...
cd /d "%FRONTEND_DIR%"
if not exist "node_modules" (
    echo [INFO] Installing frontend dependencies ^(taobao mirror^)...
    npm install --registry=https://registry.npmmirror.com
    if errorlevel 1 (
        echo [ERROR] Frontend dependency install failed
        pause
        exit /b 1
    )
) else (
    echo [OK] Frontend dependencies ready
)

:: ---- Step 6: Create data directories ----
echo.
echo [6/7] Checking data directories...
if not exist "%BACKEND_DIR%\data\db" mkdir "%BACKEND_DIR%\data\db"
if not exist "%BACKEND_DIR%\data\storage" mkdir "%BACKEND_DIR%\data\storage"
if not exist "%BACKEND_DIR%\data\celery\broker" mkdir "%BACKEND_DIR%\data\celery\broker"
if not exist "%BACKEND_DIR%\data\celery\results" mkdir "%BACKEND_DIR%\data\celery\results"
if not exist "%BACKEND_DIR%\data\chroma" mkdir "%BACKEND_DIR%\data\chroma"
if not exist "%BACKEND_DIR%\data\logs" mkdir "%BACKEND_DIR%\data\logs"
if not exist "%BACKEND_DIR%\data\models\silero-vad" mkdir "%BACKEND_DIR%\data\models\silero-vad"
echo [OK] Data directories ready

:: ---- Step 7: Start services ----
echo.
echo [7/7] Starting services...
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
