#!/bin/bash
# EngramNote 一键启动脚本 (Linux/Mac)

echo "========================================"
echo "  EngramNote 一键启动脚本"
echo "========================================"
echo ""

# 项目根目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 检查 Python 环境
# 优先使用 conda 环境，否则使用系统 Python
if command -v conda &> /dev/null; then
    eval "$(conda shell.bash hook)"
    conda activate mineru_env 2>/dev/null
    PYTHON="python"
elif [ -f "$HOME/anaconda3/envs/mineru_env/bin/python" ]; then
    PYTHON="$HOME/anaconda3/envs/mineru_env/bin/python"
else
    PYTHON="python"
fi

echo "[✓] Python 环境:"
$PYTHON --version

# 检查 Node.js
if ! command -v node &> /dev/null; then
    echo "[错误] 未找到 Node.js，请安装 Node.js 18+"
    exit 1
fi
echo "[✓] Node.js 环境:"
node --version

echo ""
echo "正在启动各服务..."
echo ""

# 启动后端 uvicorn
echo "[1/3] 启动后端服务 (uvicorn)..."
cd "$SCRIPT_DIR/backend"
$PYTHON -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload-dir app &
BACKEND_PID=$!

# 等待后端启动
sleep 3

# 启动 Celery worker
echo "[2/3] 启动 Celery worker..."
cd "$SCRIPT_DIR/backend"
$PYTHON -m celery -A app.tasks.celery_app worker --loglevel=info --pool=solo -Q celery &
CELERY_PID=$!

# 启动前端开发服务器
echo "[3/3] 启动前端开发服务器 (Vite)..."
cd "$SCRIPT_DIR/frontend"
npm run dev &
FRONTEND_PID=$!

echo ""
echo "========================================"
echo "  所有服务已启动！"
echo "  后端: http://localhost:8000"
echo "  前端: http://localhost:5173"
echo "  API 文档: http://localhost:8000/docs"
echo "========================================"
echo ""
echo "按 Ctrl+C 停止所有服务..."

# 捕获退出信号，停止所有子进程
trap "echo '正在停止服务...'; kill $BACKEND_PID $CELERY_PID $FRONTEND_PID 2>/dev/null; exit 0" INT TERM

# 等待
wait
