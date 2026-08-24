# AutoDev · 自主研发交付

AutoDev 是“云端控制台 + 本机 Codex 执行器”的全自助研发交付系统。当前推荐的正式拓扑为：

- `auto.yangtaoer.com.cn` 解析到 `162.14.113.130`，由现有 Caddy 2 提供 HTTPS。
- Caddy 回源到 `121.4.77.6:28765`，该服务器运行一键部署的控制台容器。
- `121.4.77.6` 的 28765 只允许 `162.14.113.130/32` 访问。

- 项目经理通过 `https://auto.yangtaoer.com.cn` 登录、输入 TFS 编号并查看过程与交付物；提交成功后会立即打开任务侧栏。
- 云端保存用户、只读项目目录、任务状态、审计事件和上传后的交付产物，并发送邮件。
- 你的 Windows 电脑只主动通过 HTTPS 轮询云端，不需要公网 IP、端口映射或开放防火墙入站。
- Codex 登录态、代码仓库、TFS PAT、构建环境和审核账号全部留在本机，不上传云服务器。

三种交付方式均已接入：本机打包交付、四川专用账号审核后交付、产品部人工审核后交付。SQL、配置文件、安装包、PR 链接和合并截图/凭证均可归集到云端供项目经理下载。

本机 Runner 可将生成的交付物直接上传阿里云 OSS。云端控制台和交付邮件均使用 OSS 预签名下载链接；默认链接有效期与产物保留期均为 3 天。Runner 启动时执行一次清理，之后每 72 小时删除 `autodev/` 前缀下超过 3 天的 OSS 对象及本机临时交付目录。交付邮件使用 AutoDev 品牌模板，时间统一显示为北京时间（UTC+8），发件人显示名可通过 `SMTP_FROM_NAME` 配置；所有任务邮件会自动抄送 `AUTODEV_TASK_ADMIN_EMAIL`，默认值为 `yangtao2@tellhow.com`。

## 目录

- `deploy/backend/`：`121.4.77.6` 后端服务器一键部署。
- `deploy/edge-caddy/`：`162.14.113.130` 现有 Caddy 的站点配置。
- `deploy/cloud/`：控制台与 Caddy 部署在同一台服务器时使用的备用方案。
- `local-runner/`：Windows 本机执行器安装、启动和登录自启脚本。
- `local-runner/project-presets/`：项目策略的唯一配置源；本机执行器自动同步云端只读目录。
- `local-runner/project-scripts/`：项目专用构建/打包脚本，避免每次任务临时推断命令。
- `app/`：云端控制台与本机编排器共用源码。
- `docs/deployment.md`：从 DNS 到正式运行的完整部署清单。
- `docs/architecture.md`：系统边界、三种流程与恢复行为。

## 最短上线步骤

在 `121.4.77.6` 解压部署包后：

```sh
cd deploy/backend
chmod +x *.sh
./deploy.sh
```

部署脚本已预置 163 SMTPS/465 邮件配置，会自动生成 `.env.backend`、管理员初始密码、会话密钥和本机执行器令牌，然后把应用发布到宿主机高位端口 28765。随后把 `deploy/edge-caddy/auto.yangtaoer.com.cn.caddy` 合并到 `162.14.113.130` 的现有 Caddyfile 并 reload。完整命令见 [docs/two-server-deployment.md](docs/two-server-deployment.md)。

本机 Windows 上：

```powershell
.\local-runner\install.ps1
# 编辑 local-runner\.env.runner，并安全写入 runner-token.txt 与 TFS PAT
.\local-runner\start.ps1
```

需要随 Windows 开机自动运行：

```powershell
.\local-runner\install-startup-task.ps1
```

首次执行会弹出 Windows UAC 管理员确认。该命令会注册开机、用户登录和每 5 分钟恢复三类触发器。开机初期网络或云端暂不可用时，执行器会保持运行并自动重连，不再因首次心跳失败退出。

本机运行管理：

```powershell
.\local-runner\client.ps1          # 打开图形化执行器控制台
.\local-runner\status.ps1          # 计划任务、进程、云端连通和最近日志
.\local-runner\logs.ps1 -Follow    # 持续查看执行日志
.\local-runner\stop.ps1            # 停止
.\local-runner\restart.ps1         # 重启
```

`install.ps1` 会在桌面创建“AutoDev 执行器控制台”快捷方式。该 C/S 客户端可查看当前与历史任务、任务步骤、执行器日志和打开期间的 Codex 详细输出，并可启动、停止、重启本机执行器。本机监控接口仅监听 `127.0.0.1:28766`；Codex 详细输出只在查看窗口打开期间进入内存，关闭窗口后立即停止采集且不持久化。

本机 Codex 默认复用当前电脑已有的 Codex/ChatGPT 登录态，一般不需要配置 API key。

OSS 参数配置在本机 `local-runner/.env.runner`，包括 AccessKey、Region、Endpoint、Bucket、对象前缀、链接有效期、保留天数和清理周期。OSS 凭据不会进入云端容器或一键部署 ZIP。

## 项目与账号配置

项目策略不在云端编辑。通过 Codex 对话新增或修改 `local-runner/project-presets/*.json` 后，本机执行器最迟在下一次 20 秒心跳时自动同步；管理员可在“自助项目”页面查看同步后的只读目录，项目经理不显示此管理菜单。项目经理发起需求时只输入 TFS 编号，本机执行器读取工作项 Area Path，自动匹配最具体的本机项目预设和该项目的交付方式。

项目预设关键项：

- `本机执行器 ID`：默认 `yangtao-pc`，必须与 `.env.runner` 一致。
- `本机代码仓库绝对路径`：例如 `C:\workspace\project`，由本机执行器使用。
- `TFS Collection / 项目 / Area Path`：用于需求准入和 PR 操作。
- `基础分支`：默认 `dev`。PR 审核类项目只推 feature 分支；本地打包项目会先同步最新基础分支，将本次提交推送到基础分支并校验远端提交一致，再基于该最新代码构建。
- `构建/校验命令`、安装包匹配、SQL/配置匹配和受保护路径。
- 三种交付方式之一；项目经理发起时无需选择。

当前本机目录已启用：巴中自巡航-自研（本地打包）、成都网络发令（四川自动审核）和南充网络发令（产品审核）。成都与南充均按多仓库项目处理，所有仓库统一从 `dev` 创建需求分支。

管理员可在云端创建、编辑、启用或禁用账号。每个账号支持最多 10 个通知邮箱；发起需求时从当前账号维护的邮箱中多选本次收件人，默认全部选中。

管理员任务总览展示当日任务、任务总量、成功、失败、运行中和等待合并等全局指标，同时显示本机 Codex 套餐类型、周期剩余额度、重置时间和额外余额。正在运行卡片不展示推测性百分比，只展示最近一条精简执行动态；任务处于 Codex 研发阶段时，详情侧栏提供“查看研发过程”实时会话入口。

四川自动审核方式必须配置构建/校验命令，且应使用经组织授权的专用审核账号。Codex 报告风险、构建失败或触及保护路径时，不会自动批准 PR。

## 断线与恢复

- 本机电脑关机或执行器退出时，新任务保留在云端队列，项目经理仍可正常访问控制台。
- 本机重新上线后继续领取排队任务，并继续轮询等待合并的 PR。
- 已上传云端的安装包、SQL、配置和截图不依赖本机在线。
- 为避免重复提交代码，已进入研发/提交阶段但因本机异常中断的任务不会盲目自动重跑，应先检查分支/PR 后人工处理。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试使用临时数据库和演示项目，覆盖三种交付链路、云端执行器协议、临时 Codex 实时会话、管理员看板和项目经理权限，不访问真实 TFS、不调用 Codex、不发送真实邮件。

完整部署步骤见 [docs/deployment.md](docs/deployment.md)。

## 从 GitHub 一键更新

每次代码改动完成后，本机发布流程会运行测试、推送 `main`，并通过 `scripts/publish-github.ps1` 覆盖发布固定名称的 `autodev-hybrid-latest.zip`。GitHub Actions 也保留了手工触发入口。服务器首次安装包含更新脚本的版本后，以后只需：

```sh
cd /opt/autodev/deploy/backend
./update-from-github.sh
```

脚本会下载并校验最新 ZIP、备份 SQLite 数据库与本地产物、覆盖程序文件并滚动更新容器。`.env.backend`、`secrets/` 和 `data/` 不包含在 ZIP 中，因此不会被覆盖。

需要让服务器自动跟随 GitHub `latest` 时执行一次：

```sh
cd /opt/autodev/deploy/backend
./install-auto-update.sh
```

该脚本安装 systemd timer，每 5 分钟检查一次发布包校验值。版本未变化时不做任何操作；有新版本时自动校验、备份并升级。查看状态和日志：

```sh
systemctl status autodev-github-update.timer
journalctl -u autodev-github-update.service -n 100 --no-pager
```
