#!/usr/bin/env bash
# EVALPLAT 开发环境启动脚本。
# 默认以 detached 模式启动 db / migrate / app / worker / code-worker / frontend。
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

DETACH=1
BUILD=1
ACTION="up"

usage() {
  cat <<'USAGE'
用法：./dev.sh [选项]

启动：
  ./dev.sh                  构建并后台启动完整开发环境
  ./dev.sh --foreground     构建并以前台模式启动，直接查看 Compose 日志
  ./dev.sh --no-build       启动已有镜像，不重新构建
  ./dev.sh --down           停止并移除本项目的开发容器
  ./dev.sh --help           显示帮助

可通过环境变量预设端口；端口冲突时脚本会交互式要求输入新端口：
  DB_PORT=5432               PostgreSQL（默认 5432）
  APP_PORT=18002             后端直连端口（默认 18002）
  FRONTEND_PORT=8081         前端访问端口（默认 8081）
USAGE
}

log() { printf '[dev] %s\n' "$*"; }
warn() { printf '[dev] 警告：%s\n' "$*" >&2; }
fail() { printf '[dev] 错误：%s\n' "$*" >&2; exit 1; }

while (($# > 0)); do
  case "$1" in
    --foreground) DETACH=0 ;;
    --no-build) BUILD=0 ;;
    --down) ACTION="down" ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; fail "未知选项：$1" ;;
  esac
  shift
done

if ! command -v docker >/dev/null 2>&1; then
  fail "未找到 Docker CLI，请先安装 Docker Desktop 或 Docker Engine。"
fi
if ! docker compose version >/dev/null 2>&1; then
  fail "当前 Docker CLI 不支持 'docker compose'，请安装 Compose v2。"
fi
if ! docker info >/dev/null 2>&1; then
  fail "Docker daemon 未运行或当前终端无法连接。请先启动 Docker Desktop/OrbStack/Docker Engine 后重试。"
fi

if [[ "$ACTION" == "down" ]]; then
  log "停止开发环境……"
  docker compose down
  exit 0
fi

if [[ ! -f .env ]]; then
  fail "缺少 .env。请先执行 'cp .env.example .env'，并填写 DATABASE_URL 和 APP_MASTER_KEY。"
fi

# Compose 的 code-worker 需要把宿主目录挂载到沙箱容器。
if [[ ! -S /var/run/docker.sock ]]; then
  warn "未发现 /var/run/docker.sock，code-worker 可能无法创建代码沙箱；Docker Desktop 用户通常可忽略此提示。"
fi

mkdir -p data/datasets data/evalscope data/tokenizers data/sandboxes

port_in_use() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NR > 1 { found = 1 } END { exit(found ? 0 : 1) }'
  elif command -v ss >/dev/null 2>&1; then
    ss -H -ltn "sport = :$port" 2>/dev/null | grep -q .
  elif command -v nc >/dev/null 2>&1; then
    nc -z 127.0.0.1 "$port" >/dev/null 2>&1
  else
    return 1
  fi
}

port_owner() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | tail -n +2 | sed 's/^/    /' >&2 || true
  fi
}

valid_port() {
  local candidate="$1"
  [[ "$candidate" =~ ^[0-9]+$ ]] || return 1
  local number=$((10#$candidate))
  ((number >= 1 && number <= 65535))
}

choose_port() {
  local service="$1"
  local current="$2"
  local env_name="$3"
  if ! valid_port "$current"; then
    fail "$env_name=$current 不是有效端口，端口范围必须是 1 到 65535。"
  fi
  while port_in_use "$current"; do
    warn "$service 端口 $current 已被占用。"
    port_owner "$current"
    if [[ ! -t 0 && ! -t 1 ]]; then
      fail "无法交互式更换 $service 端口，请通过环境变量指定，例如 ${env_name}=18083 ./dev.sh。"
    fi
    local replacement
    while :; do
      read -r -p "请输入 $service 的替代端口（1-65535）：" replacement </dev/tty || fail "读取端口输入失败。"
      if ! valid_port "$replacement"; then
        warn "端口必须是 1 到 65535 的数字。"
        continue
      fi
      if port_in_use "$replacement"; then
        warn "端口 $replacement 也已被占用，请重新输入。"
        port_owner "$replacement"
        continue
      fi
      current="$replacement"
      break
    done
  done
  printf '%s\n' "$current"
}

DB_PORT="$(choose_port PostgreSQL "${DB_PORT:-5432}" DB_PORT)"
APP_PORT="$(choose_port 后端 "${APP_PORT:-18002}" APP_PORT)"
FRONTEND_PORT="$(choose_port 前端 "${FRONTEND_PORT:-8081}" FRONTEND_PORT)"
export DB_PORT APP_PORT FRONTEND_PORT

# 在真正创建容器前解析一次 Compose，提前暴露环境变量或 YAML 错误。
docker compose config >/dev/null || fail "Compose 配置检查失败，请检查 .env 和 docker-compose.yml。"

compose_args=(up --remove-orphans)
if ((BUILD)); then
  compose_args+=(--build)
fi
if ((DETACH)); then
  compose_args+=(-d)
fi
compose_args+=(migrate app worker code-worker frontend)

log "使用端口：PostgreSQL=${DB_PORT}，后端=${APP_PORT}，前端=${FRONTEND_PORT}"
log "启动 db / migrate / app / worker / code-worker / frontend……"
docker compose "${compose_args[@]}"

if ((DETACH)); then
  log "容器已启动，等待前端健康检查……"
  healthy=0
  for _ in {1..30}; do
    if command -v curl >/dev/null 2>&1 && curl -fsS --max-time 2 "http://127.0.0.1:${FRONTEND_PORT}/healthz" >/dev/null 2>&1; then
      healthy=1
      break
    fi
    sleep 2
  done
  if ((healthy)); then
    log "前端已就绪：http://127.0.0.1:${FRONTEND_PORT}"
  else
    warn "前端在 60 秒内未通过健康检查，当前容器状态如下："
    docker compose ps
    warn "可使用 'docker compose logs -f app frontend worker code-worker' 查看日志。"
    exit 1
  fi
  log "后端健康检查：http://127.0.0.1:${APP_PORT}/healthz"
  log "停止环境：./dev.sh --down"
fi
