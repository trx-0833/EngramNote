#!/bin/bash
# ============================================================
# EngramNote One-Click Start Script (Linux/Mac)
# 自动检测脚本目录，无需手动配置路径
# ============================================================

set -e

# ---- 自动获取项目目录（脚本所在目录） ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"
BACKEND_PORT=8000
FRONTEND_PORT=5173

# ---- Conda 环境配置（按需修改） ----
# 如果使用 conda，取消下面两行注释并修改路径
# CONDA_BASE="$HOME/anaconda3"
# CONDA_ENV="mineru_env"
CONDA_BASE=""
CONDA_ENV="mineru_env"

echo ""
echo "============================================================"
echo "  EngramNote - One Click Start"
echo "============================================================"
echo "  Project: $PROJECT_DIR"
echo ""

# ---- 0. 运行环境检测 ----
echo "[0/5] Checking environment..."
cd "$PROJECT_DIR"
python check_env.py --check || {
    echo ""
    echo "[WARN] 环境检测发现问题，建议运行：python check_env.py --fix"
    read -p "是否继续启动？(y/N) " continue
    if [[ "$continue" != "y" && "$continue" != "Y" ]]; then
        echo "[INFO] 启动已取消。请先修复环境问题。"
        exit 1
    fi
}
echo "[OK] Environment check passed"
echo ""

# ---- 1. 激活 Conda 环境（如配置） ----
echo "[1/5] Preparing Python environment..."
if [ -n "$CONDA_BASE" ] && [ -f "$CONDA_BASE/etc/profile.d/conda.sh" ]; then
    source "$CONDA_BASE/etc/profile.d/conda.sh"
    conda activate "$CONDA_ENV" || {
        echo "[ERROR] Cannot activate conda env [$CONDA_ENV]"
        echo "        Please verify: conda env list"
        exit 1
    }
    echo "[OK] Conda env [$CONDA_ENV] activated"
else
    echo "[OK] Using system Python"
fi

# ---- 2. 检查后端依赖 ----
echo ""
echo "[2/5] Checking backend dependencies..."
cd "$BACKEND_DIR"
python -c "import fastapi" 2>/dev/null || {
    echo "[INFO] Installing backend dependencies (tsinghua mirror)..."
    pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
}
echo "[OK] Backend dependencies ready"

# ---- 3. 检查前端依赖 ----
echo ""
echo "[3/5] Checking frontend dependencies..."
cd "$FRONTEND_DIR"
if [ ! -d "node_modules" ]; then
    echo "[INFO] Installing frontend dependencies (taobao mirror)..."
    npm install --registry=https://registry.npmmirror.com
fi
echo "[OK] Frontend dependencies ready"

# ---- 4. 创建数据目录 ----
echo ""
echo "[4/5] Checking data directories..."
mkdir -p "$BACKEND_DIR/data/db"
mkdir -p "$BACKEND_DIR/data/storage"
mkdir -p "$BACKEND_DIR/data/celery/broker"
mkdir -p "$BACKEND_DIR/data/celery/results"
mkdir -p "$BACKEND_DIR/data/chroma"
mkdir -p "$BACKEND_DIR/data/logs"
mkdir -p "$BACKEND_DIR/data/models/silero-vad"
echo "[OK] Data directories ready"

# ---- 5. 启动服务 ----
echo ""
echo "[5/5] Starting services..."
echo ""

echo "[START] Backend API (port $BACKEND_PORT)..."
cd "$BACKEND_DIR"
python -m uvicorn app.main:app --reload --port "$BACKEND_PORT" --reload-dir app &
BACKEND_PID=$!

echo "Waiting 3s for backend to initialize..."
sleep 3

echo "[START] Celery Worker..."
cd "$BACKEND_DIR"
python -m celery -A app.tasks.celery_app:celery_app worker --loglevel=info &
CELERY_PID=$!

echo "[START] Frontend dev server (port $FRONTEND_PORT)..."
cd "$FRONTEND_DIR"
npm run dev &
FRONTEND_PID=$!

# ---- Done ----
echo ""
echo "============================================================"
echo "  All services started!"
echo "============================================================"
echo ""
echo "  Backend API:  http://localhost:$BACKEND_PORT"
echo "  API Docs:      http://localhost:$BACKEND_PORT/docs"
echo "  Frontend:      http://localhost:$FRONTEND_PORT"
echo ""
echo "  Press Ctrl+C to stop all services."
echo ""
echo "  Note: First file upload may take ~30s (loading embedding model)."
echo ""

# 捕获 Ctrl+C 信号，停止所有进程
trap "kill $BACKEND_PID $CELERY_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM
wait
