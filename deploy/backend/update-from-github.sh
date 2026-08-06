#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(dirname "$0")
cd "$SCRIPT_DIR"
SCRIPT_DIR=$(pwd -P)
PROJECT_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd -P)
RELEASE_BASE="https://github.com/yangtaoer/auto-dev/releases/download/latest"
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT INT TERM
MARKER_FILE="$SCRIPT_DIR/data/.installed_release_sha256"

mkdir -p "$SCRIPT_DIR/data"
if command -v flock >/dev/null 2>&1; then
  exec 9>"$SCRIPT_DIR/data/.github-update.lock"
  if ! flock -n 9; then
    echo "已有 AutoDev 更新任务正在执行，本次跳过。"
    exit 0
  fi
fi

echo "[1/6] 获取 GitHub 最新版本信息"
curl --fail --location --retry 3 \
  --header "Cache-Control: no-cache" \
  "$RELEASE_BASE/SHA256SUMS.txt" \
  --output "$TEMP_DIR/SHA256SUMS.txt"
EXPECTED_SHA=$(awk 'NR==1 {print tolower($1)}' "$TEMP_DIR/SHA256SUMS.txt")
if [ -z "$EXPECTED_SHA" ]; then
  echo "GitHub 版本校验文件无效。" >&2
  exit 1
fi
if [ "${AUTODEV_FORCE_UPDATE:-0}" != "1" ] \
    && [ -f "$MARKER_FILE" ] \
    && [ "$(tr '[:upper:]' '[:lower:]' < "$MARKER_FILE")" = "$EXPECTED_SHA" ]; then
  echo "AutoDev 已是 GitHub 最新版本，无需更新。"
  exit 0
fi

echo "[2/6] 下载 GitHub 最新部署包"
curl --fail --location --retry 3 \
  --header "Cache-Control: no-cache" \
  "$RELEASE_BASE/autodev-hybrid-latest.zip" \
  --output "$TEMP_DIR/autodev-hybrid-latest.zip"

echo "[3/6] 校验 SHA-256"
cd "$TEMP_DIR"
sha256sum --check SHA256SUMS.txt

echo "[4/6] 备份数据库与云端本地产物"
cd "$SCRIPT_DIR"
./backup.sh

echo "[5/6] 覆盖程序文件；data、secrets 和 .env.backend 不在 ZIP 中，不会被覆盖"
unzip -oq "$TEMP_DIR/autodev-hybrid-latest.zip" -d "$PROJECT_ROOT"
chmod +x "$PROJECT_ROOT"/deploy/backend/*.sh

echo "[6/6] 构建并滚动更新"
cd "$PROJECT_ROOT/deploy/backend"
./upgrade.sh
printf '%s\n' "$EXPECTED_SHA" > "$MARKER_FILE"
echo "AutoDev 自动更新完成：$EXPECTED_SHA"
