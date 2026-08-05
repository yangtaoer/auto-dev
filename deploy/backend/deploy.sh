#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(dirname "$0")
cd "$SCRIPT_DIR"
SCRIPT_DIR=$(pwd -P)
umask 077

if ! docker compose version >/dev/null 2>&1; then
  echo "需要先安装 Docker Engine 与 Docker Compose v2。" >&2
  exit 1
fi

if [ ! -f .env.backend ]; then
  cp .env.backend.example .env.backend
  echo "已根据预置配置生成 deploy/backend/.env.backend。"
fi

mkdir -p secrets data/app backups

random_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "$1"
  else
    od -An -N "$1" -tx1 /dev/urandom | tr -d ' \n'
  fi
}

[ -s secrets/autodev_secret_key.txt ] || random_secret 32 > secrets/autodev_secret_key.txt
[ -s secrets/bootstrap_admin_password.txt ] || random_secret 12 > secrets/bootstrap_admin_password.txt
[ -s secrets/runner_token.txt ] || random_secret 32 > secrets/runner_token.txt
# Compose 的 file secret 以只读 bind mount 方式提供给 UID 10001 的应用用户，
# 因此宿主机文件必须允许容器用户读取。
chmod 644 secrets/*.txt
chmod 600 .env.backend

docker compose pull permissions
docker compose build --pull web
docker compose up -d
docker compose ps

echo ""
echo "121.4.77.6 后端已启动，宿主机端口：28765（容器内部 8765）"
echo "管理员账号：admin"
echo "管理员初始密码：$SCRIPT_DIR/secrets/bootstrap_admin_password.txt"
echo "本机执行器令牌：$SCRIPT_DIR/secrets/runner_token.txt"
echo "请确认安全组仅允许 162.14.113.130/32 访问 TCP 28765。"
