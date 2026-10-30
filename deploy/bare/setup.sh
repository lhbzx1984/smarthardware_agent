#!/usr/bin/env bash
# 裸机部署（不用 Docker）：Ubuntu 22.04 / 24.04，x86_64
#   sudo bash deploy/bare/setup.sh
# 会做：装依赖 → 建 venv → 装 Python 包 → 前端 build → 写 systemd → 配 nginx → 启动
set -Eeuo pipefail

PDA_DIR="${PDA_DIR:-/opt/pda}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PDA_USER="${SUDO_USER:-root}"

info() { printf '\033[36m[info]\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m[ ok ]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[warn]\033[0m %s\n' "$*"; }
die()  { printf '\033[31m[fail]\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "请用 sudo 运行（要装系统包、写 /etc/systemd/system）。"

# ---------------------------------------------------------------- 1. 代码就位
info "部署目录：$PDA_DIR（源码：$REPO_DIR）"
if [[ "$REPO_DIR" != "$PDA_DIR" ]]; then
  mkdir -p "$PDA_DIR"
  # 用 rsync 而不是 cp：重复部署时保留 data/ workspace/，且不会把 .git 里的东西搅乱
  command -v rsync >/dev/null || apt-get install -y -qq rsync
  rsync -a --delete \
    --exclude '.git' --exclude 'node_modules' --exclude '.next' \
    --exclude 'data' --exclude 'workspace' \
    "$REPO_DIR/" "$PDA_DIR/"
fi
mkdir -p "$PDA_DIR/data" "$PDA_DIR/workspace"

# ---------------------------------------------------------------- 2. .env
if [[ ! -f "$PDA_DIR/.env" ]]; then
  cp "$PDA_DIR/deploy/env.prod.example" "$PDA_DIR/.env"
  warn "已生成 $PDA_DIR/.env，请填入真实密钥：vi $PDA_DIR/.env"
  warn "裸机部署请把 AGENT_HOST 设为 127.0.0.1（nginx 同机反代， worker 无需对外暴露）"
  exit 1
fi
chmod 600 "$PDA_DIR/.env"

# ---------------------------------------------------------------- 3. 系统依赖
info "安装系统依赖（python3-venv / nginx / nodejs 22）"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv python3-dev build-essential curl ca-certificates nginx rsync sqlite3
# node -v 形如 v22.22.2，必须先把 v 去掉，否则 -lt 拿 "v22" 做算术比较会直接报错
node_major() { command -v node >/dev/null && node -v | sed 's/^v//' | cut -d. -f1 || echo 0; }
if [[ "$(node_major)" -lt 20 ]]; then
  info "安装 Node.js 22（NodeSource）"
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y -qq nodejs
fi
node -v && npm -v

# ---------------------------------------------------------------- 4. Python 环境
info "创建 venv 并安装依赖（阿里云 PyPI 镜像）"
[[ -d "$PDA_DIR/.venv" ]] || python3 -m venv "$PDA_DIR/.venv"
"$PDA_DIR/.venv/bin/pip" install --upgrade pip -q \
  -i https://mirrors.aliyun.com/pypi/simple/
"$PDA_DIR/.venv/bin/pip" install -q \
  -i https://mirrors.aliyun.com/pypi/simple/ \
  -r "$PDA_DIR/deploy/requirements.txt"
# 想让服务器本机建模再装：.venv/bin/pip install -r deploy/requirements-cad.txt（需 x86_64 + 大内存）

# ---------------------------------------------------------------- 5. 前端构建
info "前端依赖安装与构建（需要约 1.5GB 空闲内存）"
cd "$PDA_DIR/web"
npm config set registry https://registry.npmmirror.com
npm ci --no-audit --no-fund
npm run build

# ---------------------------------------------------------------- 6. systemd
info "写入 systemd 单元"
for f in pda-worker pda-web; do
  sed -e "s|__PDA_DIR__|$PDA_DIR|g" -e "s|__PDA_USER__|$PDA_USER|g" \
    "$PDA_DIR/deploy/bare/$f.service" > "/etc/systemd/system/$f.service"
done
chown -R "$PDA_USER":"$PDA_USER" "$PDA_DIR" 2>/dev/null || true
systemctl daemon-reload
systemctl enable --now pda-worker pda-web

# ---------------------------------------------------------------- 7. nginx
info "写入 nginx 配置"
cp "$PDA_DIR/deploy/bare/nginx-host.conf" /etc/nginx/conf.d/agent.conf
nginx -t
systemctl enable --now nginx
systemctl reload nginx

# ---------------------------------------------------------------- 8. 自检
sleep 5
info "自检："
curl -fsS --max-time 5 http://127.0.0.1:8001/health && printf '\n' || warn "worker 未就绪，看日志：journalctl -u pda-worker -f"
curl -fsS --max-time 5 http://127.0.0.1/healthz && printf '\n'

ok "裸机部署完成"
cat <<EOF
  访问：http://<服务器公网IP>/
  日志：journalctl -u pda-worker -f   /   journalctl -u pda-web -f
  重启：systemctl restart pda-worker pda-web
  更新：cd $PDA_DIR && git pull && systemctl restart pda-worker pda-web（前端改动需重新 npm run build）
EOF
