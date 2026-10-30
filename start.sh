#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PORT="${PORT:-18088}"
HOST="${HOST:-127.0.0.1}"

open_browser_when_ready() {
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

if [[ "${1:-}" == "--dev" ]]; then
  FRONTEND_URL="http://127.0.0.1:5173"
  echo "启动前后端开发模式 (Backend: http://${HOST}:${PORT}, Frontend: ${FRONTEND_URL})..."
  trap 'kill 0' EXIT
  VITE_API_TARGET="http://${HOST}:${PORT}" ./.venv/bin/uvicorn backend.main:app --host "$HOST" --port "$PORT" --reload &
  (cd frontend && VITE_API_TARGET="http://${HOST}:${PORT}" npm run dev) &
  open_browser_when_ready "http://${HOST}:${PORT}/api/health" "$FRONTEND_URL"
  wait
else
  if [[ ! -d "frontend/dist" ]]; then
    echo "正在构建前端静态资源..."
    (cd frontend && npm run build)
  fi
  TARGET_URL="http://${HOST}:${PORT}"
  echo "启动 OpenAI 兼容接口测试与 AI 诊断平台：${TARGET_URL}"
  open_browser_when_ready "${TARGET_URL}/api/health" "${TARGET_URL}"
  exec ./.venv/bin/uvicorn backend.main:app --host "$HOST" --port "$PORT"
fi
