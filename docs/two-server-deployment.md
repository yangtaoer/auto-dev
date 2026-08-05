# 双服务器部署：162.14.113.130 → 121.4.77.6:28765

## 最终拓扑

```text
项目经理 / 本机执行器
          │ HTTPS 443
          ▼
auto.yangtaoer.com.cn
162.14.113.130 / Caddy 2
          │ HTTP 28765（仅此源 IP 可访问）
          ▼
121.4.77.6 / Docker / AutoDev Web
```

域名 A 记录已经解析到 `162.14.113.130`。`121.4.77.6` 不占用 80、443、8080 等常用端口，只发布高位端口 `28765`。

## 一、在 121.4.77.6 部署应用

上传并解压 `autodev-hybrid-0.2.9.zip`，例如放到 `/opt/autodev`：

```sh
cd /opt/autodev/deploy/backend
chmod +x deploy.sh upgrade.sh backup.sh
./deploy.sh
```

`.env.backend` 已默认配置：

```dotenv
PUBLIC_BASE_URL=https://auto.yangtaoer.com.cn
AUTODEV_ALLOWED_HOSTS=auto.yangtaoer.com.cn,localhost,127.0.0.1
AUTODEV_BIND_ADDRESS=0.0.0.0
AUTODEV_BACKEND_PORT=28765
```

部署脚本会自动从预置模板生成 `.env.backend`。163 邮箱已按 SMTPS/465 配置完成，无需再创建单独的密码文件：

```dotenv
SMTP_HOST=smtp.163.com
SMTP_PORT=465
SMTP_PROTOCOL=smtps
SMTP_USERNAME=yangtaoere@163.com
SMTP_FROM=yangtaoere@163.com
SMTP_STARTTLS=0
```

密码也已经写入部署模板，首次运行不需要编辑邮件配置。

检查容器：

```sh
docker compose ps
docker compose logs -f web
curl -fsS http://127.0.0.1:28765/healthz
```

预期返回：

```json
{"status":"ok","mode":"cloud-control-plane"}
```

## 二、限制 121.4.77.6 的 28765 端口

在云服务器安全组增加入站规则：

| 协议 | 端口 | 来源 | 动作 |
|---|---:|---|---|
| TCP | 28765 | `162.14.113.130/32` | 允许 |

不要添加 `0.0.0.0/0 → 28765`。如果已有面对全网的 28765 规则，应删除。

如果 Ubuntu 启用了 UFW，并且默认入站策略为 deny，再增加：

```sh
sudo ufw allow from 162.14.113.130 to any port 28765 proto tcp comment 'AutoDev edge Caddy'
sudo ufw status numbered
```

不要远程执行会重置整套防火墙的命令。先确认 SSH 端口规则保持可用。

## 三、在 162.14.113.130 配置现有 Caddy 2

部署包中的站点配置为：

```text
deploy/edge-caddy/auto.yangtaoer.com.cn.caddy
```

先从 `162.14.113.130` 验证后端连通：

```sh
curl -fsS -H 'Host: auto.yangtaoer.com.cn' http://121.4.77.6:28765/healthz
```

然后把以下站点块合并到 `/etc/caddy/Caddyfile`。如果已经存在同域名站点块，应修改原站点块，不要重复添加。

```caddyfile
auto.yangtaoer.com.cn {
    encode zstd gzip

    reverse_proxy http://121.4.77.6:28765 {
        health_uri /healthz
        health_interval 30s
        health_timeout 5s
        health_headers {
            Host auto.yangtaoer.com.cn
        }
    }

    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "strict-origin-when-cross-origin"
        Permissions-Policy "camera=(), microphone=(), geolocation=()"
        -Server
    }

    log {
        output file /var/log/caddy/autodev-access.log {
            roll_size 20MiB
            roll_keep 10
            roll_keep_for 720h
        }
        format json
    }
}
```

校验并平滑加载：

```sh
sudo caddy fmt --overwrite /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl reload caddy
sudo systemctl status caddy --no-pager
```

如果现有 Caddy 使用 Docker，请在其 Caddyfile 中加入同一个站点块，然后执行对应 Compose 项目的 `docker compose exec caddy caddy validate --config /etc/caddy/Caddyfile` 和 `docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile`。

## 四、外部验收

任意联网电脑执行：

```sh
curl -I https://auto.yangtaoer.com.cn
curl -fsS https://auto.yangtaoer.com.cn/healthz
```

浏览器打开 `https://auto.yangtaoer.com.cn`，管理员初始密码位于后端服务器：

```text
/opt/autodev/deploy/backend/secrets/bootstrap_admin_password.txt
```

随后按主 README 安装 Windows 本机执行器，把后端的 `runner_token.txt` 安全复制到本机。

## 安全说明

当前回源使用公共 IP 上的 HTTP，因此必须同时在云安全组和主机防火墙限制来源 IP。更理想的正式方案是让两台服务器通过云厂商私网或 WireGuard 通信，再把 Caddy upstream 改成对端私网地址；这样回源不会经过公网。
