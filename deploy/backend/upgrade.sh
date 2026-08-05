#!/usr/bin/env sh
set -eu
SCRIPT_DIR=$(dirname "$0")
cd "$SCRIPT_DIR"
SCRIPT_DIR=$(pwd -P)

# upgrade.sh 也允许在目录被重新创建后执行：检测到安装文件不完整时，
# 自动切换到 deploy.sh，由它补齐目录、环境文件和缺失密钥。
if [ ! -f .env.backend ] \
    || [ ! -s secrets/autodev_secret_key.txt ] \
    || [ ! -s secrets/bootstrap_admin_password.txt ] \
    || [ ! -s secrets/runner_token.txt ]; then
  echo "检测到全新或不完整安装，自动执行 deploy.sh。"
  exec "$SCRIPT_DIR/deploy.sh"
fi

# 保持 bind mount 的密钥可被容器内非 root 应用用户读取。
for secret_file in secrets/*.txt; do
  [ ! -f "$secret_file" ] || chmod 644 "$secret_file"
done
docker compose build --pull web
docker compose up -d
docker compose ps
