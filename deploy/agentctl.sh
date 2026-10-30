#!/usr/bin/env bash
# 智能产品设计智能体 —— 部署运维总入口
#   用法：bash deploy/agentctl.sh <init|up|down|restart|update|status|logs|backup|ssl>
# 设计原则：所有路径相对脚本自身定位，可在任意目录执行；出错即停（set -Eeuo pipefail）。
set -Eeuo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$DEPLOY_DIR/.." && pwd)"
COMPOSE_FILE="$DEPLOY_DIR/docker-compose.yml"
PROJECT="pda"
BACKUP_DIR="$ROOT_DIR/backups"

c_reset=$'\033[0m'; c_green=$'\033[32m'; c_yellow=$'\033[33m'; c_red=$'\033[31m'; c_cyan=$'\033[36m'
info() { printf '%s[info]%s %s\n' "$c_cyan" "$c_reset" "$*"; }
ok()   { printf '%s[ ok ]%s %s\n' "$c_green" "$c_reset" "$*"; }
warn() { printf '%s[warn]%s %s\n' "$c_yellow" "$c_reset" "$*"; }
err()  { printf '%s[fail]%s %s\n' "$c_red" "$c_reset" "$*" >&2; }
die()  { err "$*"; exit 1; }

# docker compose 有 v2 子命令与 v1 独立命令两种形态，统一成 DC 数组
detect_compose() {
  if docker compose version >/dev/null 2>&1; then
    DC=(docker compose)
  elif command -v docker-compose >/dev/null 2>&1; then
    DC=(docker-compose)
  else
    die "未检测到 docker compose。请先安装：https://docs.docker.com/engine/install/ubuntu/"
  fi
}
compose() { "${DC[@]}" -p "$PROJECT" -f "$COMPOSE_FILE" "$@"; }

require_docker() {
  command -v docker >/dev/null 2>&1 || die "未检测到 docker。"
  docker info >/dev/null 2>&1 || die "docker 守护进程不可用（服务没起，或当前用户不在 docker 组：sudo usermod -aG docker \$USER 后重登）。"
  detect_compose
}

# ---------------------------------------------------------------- .env 准备
prepare_env() {
  if [[ ! -f "$ROOT_DIR/.env" ]]; then
    cp "$DEPLOY_DIR/env.prod.example" "$ROOT_DIR/.env"
    warn "已生成 .env（来自 deploy/env.prod.example），请填入真实密钥后重跑本命令："
    warn "  vi $ROOT_DIR/.env"
    return 1
  fi
  if grep -q 'sk-xxxxxxxxxxxxxxxx' "$ROOT_DIR/.env"; then
    warn ".env 里还是占位密钥（sk-xxxxxxxxxxxxxxxx），请填真实 key：vi $ROOT_DIR/.env"
    return 1
  fi
  if ! grep -qE '^[[:space:]]*(SILICONFLOW_API_KEY|AGNES_API_KEY|OPENAI_API_KEY|DASHSCOPE_API_KEY)=.+$' "$ROOT_DIR/.env"; then
    warn ".env 里没有任何一把模型密钥，启动后会降级为演示模式（能跑流程但不出真结果）。"
  fi
  grep -q '^AGENT_HOST=0.0.0.0' "$ROOT_DIR/.env" \
    || warn ".env 未设 AGENT_HOST=0.0.0.0，容器内的 worker 只绑回环地址，nginx 会连不上。"
  mkdir -p "$ROOT_DIR/data" "$ROOT_DIR/workspace" "$DEPLOY_DIR/nginx/certs"
  return 0
}

# ---------------------------------------------------------------- 健康检查
# 注意：不要把 curl 直接接 head —— pipefail 下 head 提前关闭管道会让 curl 返回 141，
# 明明健康却判成失败。先取回文本再打印。
wait_healthy() {
  local tries=40 i body
  for ((i = 1; i <= tries; i++)); do
    if curl -fsS --max-time 3 http://127.0.0.1/healthz >/dev/null 2>&1; then
      ok "nginx 就绪"
      if body="$(curl -fsS --max-time 5 http://127.0.0.1/api/agent/health 2>/dev/null)"; then
        printf '%s\n' "$body"
        return 0
      fi
    fi
    sleep 3
  done
  warn "等待超时，看日志排查：bash deploy/agentctl.sh logs worker"
  return 1
}

# ---------------------------------------------------------------- 命令
cmd_init() {
  require_docker
  prepare_env || exit 1
  info "构建镜像（首次约 5~10 分钟，已开阿里云 PyPI / npmmirror 加速）"
  compose build
  info "启动服务"
  compose up -d
  wait_healthy || true
  cmd_status
  cat <<EOF

${c_green}部署完成${c_reset}
  访问：http://<服务器公网IP>/
  若打不开：确认阿里云安全组已放行 80（443）端口
  常用：bash deploy/agentctl.sh status|logs|restart|update|backup
EOF
}

cmd_up()      { require_docker; prepare_env || exit 1; compose up -d; wait_healthy || true; }
cmd_down()    { require_docker; compose down; ok "已停止（数据与产物保留在 data/ 与 workspace/）"; }
cmd_restart() { require_docker; compose restart; wait_healthy || true; }

cmd_update() {
  require_docker
  info "拉取代码"
  git -C "$ROOT_DIR" pull --ff-only || warn "git pull 失败（有本地改动？），继续使用当前代码"
  git -C "$ROOT_DIR" submodule update --init 2>/dev/null || true
  info "重新构建并滚动启动"
  compose build --pull
  compose up -d --remove-orphans
  wait_healthy || true
  ok "更新完成"
}

cmd_status() {
  require_docker
  compose ps
  echo
  info "worker 直连自检："
  if body="$(docker exec pda-worker curl -fsS http://127.0.0.1:8001/health 2>/dev/null)"; then
    printf '%s\n' "$body"
  else
    warn "worker 未响应"
  fi
  info "磁盘占用："
  du -sh "$ROOT_DIR/data" "$ROOT_DIR/workspace" 2>/dev/null || true
}

cmd_logs() {
  require_docker
  local svc="${1:-}"
  if [[ -n "$svc" ]]; then compose logs -f --tail=200 "$svc"; else compose logs -f --tail=200; fi
}

cmd_backup() {
  mkdir -p "$BACKUP_DIR"
  local stamp; stamp="$(date +%Y%m%d-%H%M%S)"
  local out="$BACKUP_DIR/pda-$stamp.tar.gz"
  # sqlite 在线备份：WAL 模式下直接 tar 可能拿到撕裂的库，先在容器内做一次一致性快照
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx pda-worker; then
    info "在线快照 sqlite（避免 WAL 撕裂）"
    # -i 不能少：不加 -i，docker exec 不开 stdin，后面这段 heredoc 根本进不去容器
    docker exec -i pda-worker python - <<'PY' || warn "在线快照失败，退回直接打包"
import sqlite3
src, bak = "/app/data/agent.db", "/app/data/agent.db.snapshot"
con = sqlite3.connect(src)
dst = sqlite3.connect(bak)
con.backup(dst)
dst.close(); con.close()
print("snapshot ok")
PY
  fi
  info "打包 data/ 与 workspace/ → $out"
  tar -czf "$out" -C "$ROOT_DIR" data workspace 2>/dev/null || die "打包失败"
  # 只保留最近 7 份，避免把系统盘撑满
  ls -1t "$BACKUP_DIR"/pda-*.tar.gz 2>/dev/null | tail -n +8 | xargs -r rm -f
  ok "备份完成：$out（$(du -h "$out" | cut -f1)）"
}

cmd_ssl() {
  # 用 certbot 签一份 Let's Encrypt 证书，签完把配置模板落地并重载 nginx
  local domain="${1:-}"
  [[ -n "$domain" ]] || die "用法：bash deploy/agentctl.sh ssl your-domain.com（域名需已解析到本机）"
  command -v certbot >/dev/null 2>&1 || {
    info "安装 certbot"
    (command -v apt-get >/dev/null && sudo apt-get update -qq && sudo apt-get install -y -qq certbot) \
      || die "请手动安装 certbot：https://certbot.eff.org/instructions"
  }
  # standalone 模式要独占 80 端口，nginx 必须先让位，否则 certbot 起不来
  info "临时停 nginx 让出 80 端口"
  compose stop nginx || true
  sudo certbot certonly --standalone -d "$domain" --agree-tos -m "admin@$domain" --non-interactive
  sudo cp "/etc/letsencrypt/live/$domain/fullchain.pem" "$DEPLOY_DIR/nginx/certs/fullchain.pem"
  sudo cp "/etc/letsencrypt/live/$domain/privkey.pem"   "$DEPLOY_DIR/nginx/certs/privkey.pem"
  sudo chmod 644 "$DEPLOY_DIR/nginx/certs/"*.pem
  sed "s/your-domain.com/$domain/g" "$DEPLOY_DIR/nginx/conf.d/agent-ssl.conf.example" \
    > "$DEPLOY_DIR/nginx/conf.d/agent-ssl.conf"
  info "放开 443：编辑 deploy/docker-compose.yml 取消注释 \"443:443\""
  compose up -d nginx
  cat <<EOF
${c_green}HTTPS 已配置${c_reset}
  https://$domain/
  证书 90 天到期，续期：sudo certbot renew && bash deploy/agentctl.sh ssl $domain
EOF
}

usage() {
  cat <<EOF
用法：bash deploy/agentctl.sh <命令> [参数]

  init      首次部署：生成 .env → 构建镜像 → 启动 → 健康检查
  up        启动（.env 已就绪时）
  down      停止并移除容器（保留 data/ 与 workspace/）
  restart   重启
  update    git pull + 重新构建 + 滚动启动
  status    查看容器状态 / worker 自检 / 磁盘占用
  logs [worker|web|nginx]   跟随日志
  backup    备份 data/ 与 workspace/ 到 backups/（保留最近 7 份）
  ssl <域名> 申请 Let's Encrypt 证书并切换到 HTTPS
EOF
}

main() {
  local cmd="${1:-}"; shift || true
  case "$cmd" in
    init)    cmd_init ;;
    up)      cmd_up ;;
    down)    cmd_down ;;
    restart) cmd_restart ;;
    update)  cmd_update ;;
    status)  cmd_status ;;
    logs)    cmd_logs "$@" ;;
    backup)  cmd_backup ;;
    ssl)     cmd_ssl "$@" ;;
    ""|-h|--help|help) usage ;;
    *) usage; die "未知命令：$cmd" ;;
  esac
}

main "$@"
