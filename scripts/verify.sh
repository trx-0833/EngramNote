#!/usr/bin/env bash
# EngramNote 统一验证入口
#
# 用法（Git Bash / WSL）：
#   bash scripts/verify.sh          # 快档：ruff + eslint + vitest，约 30–60s
#   bash scripts/verify.sh full     # 全档：加 pytest + 覆盖率 + build，约 5–10min
#
# 覆盖 Python 路径：
#   PYTHON=/d/conda/envs/aiforlearn/python.exe bash scripts/verify.sh
#
# 说明：
#   - 逐项执行、汇总失败；不在第一个失败处中断（便于一次看全）。
#   - 只读不写（除测试自身产生的临时文件）。
#   - 本脚本当前【未接入 CI】。接入需单独改 .github/workflows/ci.yml。

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2
ROOT="$(pwd)"
MODE="${1:-fast}"

# ---- 解释器解析 ----------------------------------------------------------
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for cand in \
    "/d/conda/envs/aiforlearn/python.exe" \
    "/d/conda/envs/aiforlearn/bin/python" \
    "python" "python3"
  do
    if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
    if [ -x "$cand" ]; then PY="$cand"; break; fi
  done
fi

echo "repo   : $ROOT"
echo "mode   : $MODE"
echo "python : ${PY:-<not found>}"
echo

FAIL=0
FAILED_STEPS=""

run() {
  local label="$1"; shift
  printf '==> %-28s' "$label"
  local t0 t1 out
  t0=$(date +%s)
  if out=$("$@" 2>&1); then
    t1=$(date +%s)
    printf ' OK (%ss)\n' "$((t1 - t0))"
  else
    t1=$(date +%s)
    printf ' FAIL (%ss)\n' "$((t1 - t0))"
    printf '%s\n' "$out" | sed 's/^/    | /' | tail -n 40
    FAIL=1
    FAILED_STEPS="${FAILED_STEPS}${label}; "
  fi
}

have() { command -v "$1" >/dev/null 2>&1; }

# ---- 后端 ----------------------------------------------------------------
if [ -d backend ]; then
  if [ -n "$PY" ]; then
    run "backend: ruff check" bash -c "cd backend && '$PY' -m ruff check app tests scripts"
  else
    echo "==> backend: ruff check          SKIP (python 未找到)"
  fi

  if [ "$MODE" = "full" ] && [ -n "$PY" ]; then
    run "backend: pytest" bash -c "cd backend && '$PY' -m pytest -q"
    run "backend: coverage" bash -c "cd backend && '$PY' -m pytest -q --cov=app --cov-report=term-missing"
  fi
fi

# ---- 前端 ----------------------------------------------------------------
if [ -d frontend ] && have npm; then
  run "frontend: eslint" bash -c "cd frontend && npm run lint --silent"
  run "frontend: vitest" bash -c "cd frontend && npx vitest run --reporter=dot"

  if [ "$MODE" = "full" ]; then
    run "frontend: prettier check" bash -c "cd frontend && npm run format:check --silent"
    run "frontend: build" bash -c "cd frontend && npm run build --silent"
  fi
elif [ -d frontend ]; then
  echo "==> frontend                      SKIP (npm 未找到)"
fi

# ---- 汇总 ----------------------------------------------------------------
echo
if [ "$FAIL" -ne 0 ]; then
  echo "❌ 失败项：$FAILED_STEPS"
  echo "   提示：单项复跑可加 --silent 关闭无关输出，或直接跑上面的命令。"
  exit 1
fi
echo "✅ 全部通过（mode=$MODE）"
