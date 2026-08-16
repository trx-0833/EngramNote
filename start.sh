#!/bin/bash
# ============================================================
# EngramNote One-Click Start Script (Linux/Mac)
# 自动检测脚本目录，无需手动配置路径
# ============================================================

# 注意：不使用 set -e，因为它会与后台进程冲突

# ---- 自动获取项目目录（脚本所在目录） ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"
BACKEND_PORT=8000
FRONTEND_PORT=5173

# ---- Conda 环境配置（自动检测） ----
CONDA_ENV="mineru_env"
CONDA_BASE=""

for cb in "$HOME/anaconda3" "$HOME/miniconda3" "/opt/anaconda3" "/opt/miniconda3"; do
    if [ -f "$cb/etc/profile.d/conda.sh" ]; then
        CONDA_BASE="$cb"
        break
    fi
done

echo ""
echo "============================================================"
echo "  EngramNote - One Click Start"
echo "============================================================"
echo "  Project: $PROJECT_DIR"
echo ""

# ---- 1. 激活 Conda 环境 ----
echo "[1/7] Preparing Python environment..."
if [ -n "$CONDA_BASE" ] && [ -f "$CONDA_BASE/etc/profile.d/conda.sh" ]; then
    source "$CONDA_BASE/etc/profile.d/conda.sh"
    if ! conda activate "$CONDA_ENV"; then
        echo "[ERROR] Cannot activate conda env [$CONDA_ENV]"
        echo "        Please verify: conda env list"
        exit 1
    fi
    echo "[OK] Conda env [$CONDA_ENV] activated"
else
    echo "[WARN] 未检测到 conda，使用系统 Python"
    echo "       如需使用 conda，请编辑本脚本顶部的 CONDA_BASE"
fi

# ---- 2. 检测 Node.js/npm 路径 ----
echo ""
echo "[2/7] Checking Node.js/npm path..."
if command -v npm >/dev/null 2>&1; then
    echo "[OK] npm 已在 PATH 中: $(command -v npm)"
else
    echo "[WARN] npm 不在 PATH 中，尝试检测 nvm 路径..."
    # 检测 nvm 常见路径
    NVM_DIR_PATH=""
    for nd in "$HOME/.nvm/versions/node" "/usr/local/nvm/versions/node" "/usr/share/nvm/versions/node"; do
        if [ -d "$nd" ]; then
            # 查找最新版本
            LATEST_NODE=$(ls -v "$nd" 2>/dev/null | tail -n 1)
            if [ -n "$LATEST_NODE" ] && [ -x "$nd/$LATEST_NODE/bin/npm" ]; then
                NVM_DIR_PATH="$nd/$LATEST_NODE/bin"
                break
            fi
        fi
    done

    if [ -n "$NVM_DIR_PATH" ]; then
        export PATH="$NVM_DIR_PATH:$PATH"
        echo "[OK] 已将 Node.js 路径加入 PATH: $NVM_DIR_PATH"
    else
        echo "[ERROR] 未检测到 npm，请安装 Node.js 18+"
        echo "        下载地址: https://nodejs.org/"
        exit 1
    fi
fi

# ---- 3. 运行环境检测 ----
echo ""
echo "[3/7] Checking environment..."
cd "$PROJECT_DIR"
if ! python check_env.py --check; then
    echo ""
    echo "[WARN] 环境检测发现问题，建议运行：python check_env.py --fix"
    read -p "是否继续启动？(y/N) " continue
    if [[ "$continue" != "y" && "$continue" != "Y" ]]; then
        echo "[INFO] 启动已取消。请先修复环境问题。"
        exit 1
    fi
fi
echo "[OK] Environment check passed"

# ---- 4. 检查后端依赖 ----
echo ""
echo "[4/7] Checking backend dependencies..."
cd "$BACKEND_DIR"
if ! python -c "import fastapi" 2>/dev/null; then
    echo "[INFO] Installing backend dependencies (tsinghua mirror)..."
    if ! pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn; then
        echo "[ERROR] Backend dependency install failed"
        exit 1
    fi
fi
echo "[OK] Backend dependencies ready"

# ---- 5. 检查前端依赖 ----
echo ""
echo "[5/7] Checking frontend dependencies..."
cd "$FRONTEND_DIR"
if [ ! -d "node_modules" ]; then
    echo "[INFO] Installing frontend dependencies (taobao mirror)..."
    if ! npm install --registry=https://registry.npmmirror.com; then
        echo "[ERROR] Frontend dependency install failed"
        exit 1
    fi
fi
echo "[OK] Frontend dependencies ready"

# ---- 6. 创建数据目录 ----
echo ""
echo "[6/7] Checking data directories..."
mkdir -p "$BACKEND_DIR/data/db"
mkdir -p "$BACKEND_DIR/data/storage"
mkdir -p "$BACKEND_DIR/data/celery/broker"
mkdir -p "$BACKEND_DIR/data/celery/results"
mkdir -p "$BACKEND_DIR/data/chroma"
mkdir -p "$BACKEND_DIR/data/logs"
mkdir -p "$BACKEND_DIR/data/models/silero-vad"
echo "[OK] Data directories ready"

# ---- 7. 启动服务 ----
echo ""
echo "[7/7] Starting services..."
echo ""

echo "[START] Backend API (port $BACKEND_PORT)..."
cd "$BACKEND_DIR"
python -m uvicorn app.main:app --reload --port "$BACKEND_PORT" --reload-dir app &
BACKEND_PID=$!

echo "Waiting 3s for backend to initialize..."
sleep 3

echo "[START] Celery Worker..."
cd "$BACKEND_DIR"
# Linux 不需要 --pool=solo（那是 Windows 专用），使用默认 prefork 池
python -m celery -A app.tasks.celery_app:celery_app worker --loglevel=info &
CELERY_PID=$!

# 检测并清理残留的 Beat pidfile（旧进程已不存在时）
SKIP_BEAT=0
BEAT_PID=""
if [ -f "$BACKEND_DIR/data/celery/beat.pid" ]; then
    OLD_BEAT_PID=$(cat "$BACKEND_DIR/data/celery/beat.pid" 2>/dev/null | tr -d '[:space:]')
    if [ -n "$OLD_BEAT_PID" ] && kill -0 "$OLD_BEAT_PID" 2>/dev/null; then
        echo "[WARN] 检测到 Beat 正在运行 (PID $OLD_BEAT_PID)，跳过 Beat 启动"
        SKIP_BEAT=1
    else
        echo "[INFO] 清理残留 Beat pidfile（旧进程已不存在）"
        rm -f "$BACKEND_DIR/data/celery/beat.pid"
    fi
fi

if [ "$SKIP_BEAT" = "0" ]; then
    echo "[START] Celery Beat (F-10: 定时任务 00:30 目标刷新 / 09:00 复习邮件)..."
    cd "$BACKEND_DIR"
    python -m celery -A app.tasks.celery_app:celery_app beat --loglevel=info --pidfile "$BACKEND_DIR/data/celery/beat.pid" &
    BEAT_PID=$!
fi

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

# 捕获 Ctrl+C 信号，停止所有进程（F-10：清理列表包含 Beat）
cleanup() {
    echo ""
    echo "[STOP] 正在停止所有服务..."
    kill $BACKEND_PID $CELERY_PID $BEAT_PID $FRONTEND_PID 2>/dev/null
    wait $BACKEND_PID $CELERY_PID $BEAT_PID $FRONTEND_PID 2>/dev/null
    rm -f "$BACKEND_DIR/data/celery/beat.pid"
    echo "[OK] 所有服务已停止"
    exit 0
}
trap cleanup INT TERM

# 等待所有后台进程
wait
