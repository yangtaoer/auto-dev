#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(dirname "$0")
cd "$SCRIPT_DIR"
SCRIPT_DIR=$(pwd -P)

if [ "$(id -u)" -ne 0 ]; then
  echo "请使用 root 执行自动更新安装脚本。" >&2
  exit 1
fi
if ! command -v systemctl >/dev/null 2>&1; then
  echo "当前服务器不支持 systemd，无法安装自动更新定时器。" >&2
  exit 1
fi

cat > /etc/systemd/system/autodev-github-update.service <<EOF
[Unit]
Description=AutoDev update from GitHub latest release
Wants=network-online.target
After=network-online.target docker.service

[Service]
Type=oneshot
WorkingDirectory=$SCRIPT_DIR
ExecStart=/bin/sh $SCRIPT_DIR/update-from-github.sh
TimeoutStartSec=45min
Nice=10
EOF

cat > /etc/systemd/system/autodev-github-update.timer <<'EOF'
[Unit]
Description=Check AutoDev GitHub release every five minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
RandomizedDelaySec=30s
Persistent=true
Unit=autodev-github-update.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now autodev-github-update.timer
echo "AutoDev GitHub 自动更新已启用：每 5 分钟检查一次，有新版本才备份并升级。"
systemctl --no-pager status autodev-github-update.timer || true
