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

if [ ! -f .env.production ]; then
  cp .env.production.example .env.production
  echo "已生成 deploy/cloud/.env.production，请先填写 CADDY_EMAIL 和 SMTP 配置后再次运行。"
  exit 2
fi
if grep -q "replace-with-your-email" .env.production; then
  echo "请先在 .env.production 中填写真实 CADDY_EMAIL。" >&2
  exit 2
fi

mkdir -p secrets data/app data/caddy-data data/caddy-config data/caddy-logs

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
[ -f secrets/smtp_password.txt ] || : > secrets/smtp_password.txt
# Compose 的 file secret 以只读 bind mount 方式提供给 UID 10001 的应用用户。
chmod 644 secrets/*.txt
chmod 600 .env.production

docker compose pull permissions caddy
docker compose build --pull web
docker compose up -d
docker compose ps

echo ""
echo "部署已启动：https://auto.yangtaoer.com.cn"
echo "管理员账号：admin"
echo "管理员初始密码文件：$SCRIPT_DIR/secrets/bootstrap_admin_password.txt"
echo "把 $SCRIPT_DIR/secrets/runner_token.txt 的内容安全复制到本机执行器 secrets/runner-token.txt。"
