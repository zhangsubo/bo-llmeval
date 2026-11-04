#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

HOST="${HOST:-127.0.0.1}"

# 识别 Git 主工作区目录（支持在多个非 main worktree 下复用主仓库 .venv 与配置）
MAIN_WORKTREE="$ROOT_DIR"
if command -v git >/dev/null 2>&1; then
  GIT_COMMON_DIR="$(git -C "$ROOT_DIR" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
  if [[ -z "$GIT_COMMON_DIR" ]]; then
    RAW_COMMON="$(git -C "$ROOT_DIR" rev-parse --git-common-dir 2>/dev/null || true)"
    if [[ -n "$RAW_COMMON" ]]; then
      GIT_COMMON_DIR="$(cd "$ROOT_DIR" && cd "$RAW_COMMON" && pwd)"
    fi
  fi
  if [[ -n "$GIT_COMMON_DIR" && "$(basename "$GIT_COMMON_DIR")" == ".git" ]]; then
    MAIN_WORKTREE="$(dirname "$GIT_COMMON_DIR")"
  fi
fi

IS_LINKED_WORKTREE=0
if [[ "$MAIN_WORKTREE" != "$ROOT_DIR" ]]; then
  IS_LINKED_WORKTREE=1
fi

# 优先使用当前 worktree 的 .venv，其次回退到主工作区 .venv，最后尝试系统 python3
if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
elif [[ -x "$MAIN_WORKTREE/.venv/bin/python" ]]; then
  PYTHON_BIN="$MAIN_WORKTREE/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
else
  echo "错误：未找到可用的 Python 环境（已检查 $ROOT_DIR/.venv 与 $MAIN_WORKTREE/.venv）。" >&2
  exit 1
fi

if ! "$PYTHON_BIN" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
  echo "错误：Python 环境 ($PYTHON_BIN) 缺少 fastapi 或 uvicorn 依赖。" >&2
  exit 1
fi

# 确保当前 worktree 的代码优先于主仓库 .venv 中可能存在的 editable 路径
export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"

# 非 main worktree 若未单独创建本地配置文件，默认复用主工作区的 1Password 引用配置
if [[ "$IS_LINKED_WORKTREE" -eq 1 && -z "${BO_LLMEVAL_CONFIG_PATH:-}" ]]; then
  if [[ ! -f "$ROOT_DIR/scripts/openai-api-config" && -f "$MAIN_WORKTREE/scripts/openai-api-config" ]]; then
    export BO_LLMEVAL_CONFIG_PATH="$MAIN_WORKTREE/scripts/openai-api-config"
  fi
fi

is_port_free() {
  local check_host="$1"
  local check_port="$2"
  "$PYTHON_BIN" - "$check_host" "$check_port" <<'PY'
import socket
import sys

host, port = sys.argv[1], int(sys.argv[2])
probe_hosts = {host, '127.0.0.1', 'localhost'} if host in ('127.0.0.1', '0.0.0.0', 'localhost') else {host}
for h in probe_hosts:
    try:
        with socket.create_connection((h, port), timeout=0.15):
            sys.exit(1)
    except OSError:
        pass

bind_host = '127.0.0.1' if host == 'localhost' else host
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    try:
        s.bind((bind_host, port))
    except OSError:
        sys.exit(1)
PY
}

select_available_port() {
  local explicit_port="$1"
  local start_port="$2"
  local label="$3"

  if [[ -n "$explicit_port" ]]; then
    if ! is_port_free "$HOST" "$explicit_port"; then
      echo "错误：指定的${label}端口 ${HOST}:${explicit_port} 已被占用，请更换端口后重试。" >&2
      exit 1
    fi
    echo "$explicit_port"
    return 0
  fi

  local cand
  for ((cand = start_port; cand <= start_port + 100; cand++)); do
    if is_port_free "$HOST" "$cand"; then
      echo "$cand"
      return 0
    fi
  done

  echo "错误：在 ${start_port}~$((start_port + 100)) 范围内未找到可用的${label}端口。" >&2
  exit 1
}

DEFAULT_BACKEND_START_PORT=$((18088 + IS_LINKED_WORKTREE))
PORT="$(select_available_port "${PORT:-}" "$DEFAULT_BACKEND_START_PORT" "后端")"

ensure_frontend_deps() {
  if [[ ! -d "frontend/node_modules" ]]; then
    echo "未检测到 frontend/node_modules，正在安装前端依赖..."
    (cd frontend && npm ci)
  fi
}

open_browser_when_ready() {
  if [[ "${NO_OPEN:-0}" == "1" ]]; then
    return 0
  fi
  local check_url="$1"
  local open_url="$2"
  (
    for _ in {1..40}; do
      if curl -fsS "$check_url" >/dev/null 2>&1; then
        if command -v open >/dev/null 2>&1; then
          open "$open_url"
        elif command -v xdg-open >/dev/null 2>&1; then
          xdg-open "$open_url"
        fi
        break
      fi
      sleep 0.25
    done
  ) &
}

WORKTREE_LABEL=""
if [[ "$IS_LINKED_WORKTREE" -eq 1 ]]; then
  WORKTREE_LABEL=" [worktree: $(basename "$ROOT_DIR")]"
fi

if [[ "${1:-}" == "--dev" ]]; then
  ensure_frontend_deps
  DEFAULT_FRONTEND_START_PORT=$((5173 + IS_LINKED_WORKTREE))
  FRONTEND_PORT="$(select_available_port "${FRONTEND_PORT:-}" "$DEFAULT_FRONTEND_START_PORT" "前端开发")"
  FRONTEND_URL="http://${HOST}:${FRONTEND_PORT}"
  echo "启动前后端开发模式${WORKTREE_LABEL} (Backend: http://${HOST}:${PORT}, Frontend: ${FRONTEND_URL})..."
  trap 'kill 0' EXIT
  VITE_API_TARGET="http://${HOST}:${PORT}" "$PYTHON_BIN" -m uvicorn backend.main:app --host "$HOST" --port "$PORT" --reload &
  (cd frontend && VITE_PORT="$FRONTEND_PORT" VITE_API_TARGET="http://${HOST}:${PORT}" npm run dev) &
  open_browser_when_ready "http://${HOST}:${PORT}/api/health" "$FRONTEND_URL"
  wait
else
  if [[ ! -f "frontend/dist/index.html" ]] || [[ -n "$(find frontend/src frontend/index.html -newer frontend/dist/index.html 2>/dev/null | head -n 1)" ]]; then
    ensure_frontend_deps
    echo "正在构建前端静态资源..."
    (cd frontend && npm run build)
  fi
  TARGET_URL="http://${HOST}:${PORT}"
  echo "启动 OpenAI 兼容接口测试与 AI 诊断平台${WORKTREE_LABEL}：${TARGET_URL}"
  if [[ -n "${BO_LLMEVAL_CONFIG_PATH:-}" ]]; then
    echo "复用配置文件：${BO_LLMEVAL_CONFIG_PATH}"
  fi
  open_browser_when_ready "${TARGET_URL}/api/health" "${TARGET_URL}"
  exec "$PYTHON_BIN" -m uvicorn backend.main:app --host "$HOST" --port "$PORT"
fi
