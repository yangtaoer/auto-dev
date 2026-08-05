首次执行 `deploy.sh` 会自动创建以下文件，它们不会被打入后续源码提交：

- `autodev_secret_key.txt`
- `bootstrap_admin_password.txt`
- `runner_token.txt`
- `smtp_password.txt`

如需 SMTP 密码，部署前或部署后写入 `smtp_password.txt`，再执行 `docker compose up -d`。
