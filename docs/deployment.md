# 部署手册：auto.yangtaoer.com.cn

> 当前实际使用的是双服务器拓扑：Caddy 在 `162.14.113.130`，应用在 `121.4.77.6:28765`。请优先按照 [two-server-deployment.md](two-server-deployment.md) 部署。本页保留为“Caddy 与应用同机”时的备用说明。

## 1. 准备 DNS 与服务器

1. 为 `auto.yangtaoer.com.cn` 添加指向服务器公网 IP 的 A 记录；只有确实配置 IPv6 时才添加 AAAA 记录。
2. 安装 Docker Engine 和 Docker Compose v2。
3. 在云防火墙和系统防火墙开放 TCP 80、TCP 443；UDP 443 用于 HTTP/3，可选。
4. 不需要为 Windows 本机开放任何入站端口。

Caddy 会自动申请和续期 HTTPS 证书，因此部署时域名必须已经解析到当前服务器，并且 80/443 能从公网访问。

## 2. 部署云端控制台

把整个压缩包上传并解压到服务器，例如 `/opt/autodev`：

```sh
cd /opt/autodev/deploy/cloud
cp .env.production.example .env.production
vi .env.production
chmod +x deploy.sh upgrade.sh backup.sh
./deploy.sh
```

`.env.production` 至少修改：

```dotenv
CADDY_EMAIL=你的证书通知邮箱
SMTP_HOST=你的SMTP服务器
SMTP_PORT=587
SMTP_USERNAME=SMTP账号
SMTP_FROM=交付发件人地址
SMTP_STARTTLS=1
```

SMTP 密码写入 `deploy/cloud/secrets/smtp_password.txt`。如果暂不配置 SMTP，系统仍会生成邮件预览文件，但不会真的发信。

首次运行会生成：

- `secrets/bootstrap_admin_password.txt`：`admin` 的初始密码。
- `secrets/autodev_secret_key.txt`：登录会话密钥。
- `secrets/runner_token.txt`：本机执行器共享令牌。

检查服务：

```sh
docker compose ps
docker compose logs -f web caddy
curl -fsS https://auto.yangtaoer.com.cn/healthz
```

## 3. 安装 Windows 本机执行器

在需要运行 Codex、已有代码仓库并能访问 TFS 的电脑上安装 Python 3.11+ x64，然后以普通用户 PowerShell 执行：

```powershell
Set-Location "C:\你的路径\全自助需求研发交付"
.\local-runner\install.ps1
```

编辑 `local-runner\.env.runner`，确认：

```dotenv
AUTODEV_CLOUD_URL=https://auto.yangtaoer.com.cn
AUTODEV_RUNNER_ID=yangtao-pc
```

然后：

1. 把云端 `secrets/runner_token.txt` 的内容安全写入本机 `local-runner\secrets\runner-token.txt`。
2. 把 TFS PAT 写入 `tfs-pat.txt`。
3. 仅四川自动审核需要把专用审核账号 PAT 写入 `tfs-reviewer-pat.txt`，并配置 `TFS_REVIEWER_ID`。
4. 确认当前 Windows 用户可以正常使用 Codex；默认复用本机已有登录态。
5. 先运行 `.\local-runner\start.ps1`，云端左下角应在约 20 秒内显示 `yangtao-pc` 在线。

如启用 OSS 交付，在 `local-runner/.env.runner` 中配置 `ALIYUN_ACCESS_KEY_ID`、`ALIYUN_ACCESS_KEY_SECRET`、`ALIYUN_OSS_REGION`、`ALIYUN_OSS_ENDPOINT` 和 `ALIYUN_OSS_BUCKET`。默认对象前缀为 `autodev`，签名链接和产物保留期为 3 天，Runner 每 72 小时执行一次 OSS 与本机交付目录清理。

验收完成后注册登录自启：

```powershell
.\local-runner\install-startup-task.ps1
.\local-runner\status.ps1
```

日常管理命令：

```powershell
.\local-runner\logs.ps1 -Follow
.\local-runner\stop.ps1
.\local-runner\restart.ps1
```

## 4. 云端配置项目

管理员打开 `https://auto.yangtaoer.com.cn`：

1. 新建项目经理账号并填写真实交付邮箱。
2. 新建项目策略，执行器 ID 填 `yangtao-pc`。
3. 仓库路径填写 Windows 本机的绝对路径，例如 `D:\workspace\sc-project`。
4. 配置允许的 TFS Area Path、基础分支、构建命令和产物匹配。
5. 先保持演示模式跑通所选交付方式，再关闭演示模式验证真实 TFS 需求。

## 5. 运维

升级：

```sh
cd /opt/autodev/deploy/cloud
./upgrade.sh
```

备份数据库和交付物：

```sh
./backup.sh
```

备份文件保存在 `deploy/cloud/backups/`。还应把 `secrets/` 独立保存在受控密码库中。不要把 `.env.production`、secret 文件、数据库或交付物提交到 Git。

## 6. Caddy 2 配置

正式配置在 `deploy/cloud/Caddyfile`，核心内容如下：

```caddyfile
auto.yangtaoer.com.cn {
    encode zstd gzip
    reverse_proxy web:8765
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "strict-origin-when-cross-origin"
    }
}
```

Compose 内应使用 `web:8765`；如果 Caddy 直接安装在宿主机，则把上面的反向代理地址改为仅监听回环地址的 Web 端口，例如 `127.0.0.1:8765`，并且不要再启动 Compose 中的 Caddy 服务。
