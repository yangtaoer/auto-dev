#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(dirname "$0")
cd "$SCRIPT_DIR"
SCRIPT_DIR=$(pwd -P)
PROJECT_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd -P)
RELEASE_BASE="https://github.com/yangtaoer/auto-dev/releases/download/latest"
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT INT TERM

echo "[1/5] 下载 GitHub 最新部署包"
curl --fail --location --retry 3 \
  "$RELEASE_BASE/autodev-hybrid-latest.zip" \
  --output "$TEMP_DIR/autodev-hybrid-latest.zip"
curl --fail --location --retry 3 \
  "$RELEASE_BASE/SHA256SUMS.txt" \
  --output "$TEMP_DIR/SHA256SUMS.txt"

echo "[2/5] 校验 SHA-256"
cd "$TEMP_DIR"
sha256sum --check SHA256SUMS.txt

echo "[3/5] 备份数据库与云端本地产物"
cd "$SCRIPT_DIR"
./backup.sh

echo "[4/5] 覆盖程序文件；data、secrets 和 .env.backend 不在 ZIP 中，不会被覆盖"
unzip -oq "$TEMP_DIR/autodev-hybrid-latest.zip" -d "$PROJECT_ROOT"
chmod +x "$PROJECT_ROOT"/deploy/backend/*.sh

echo "[5/5] 构建并滚动更新"
cd "$PROJECT_ROOT/deploy/backend"
exec ./upgrade.sh
