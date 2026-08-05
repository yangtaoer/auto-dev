本目录只保存本机执行器所需凭据。`install.ps1` 会创建空文件：

- `runner-token.txt`：必须与云端 `deploy/cloud/secrets/runner_token.txt` 一致。
- `tfs-pat.txt`：提交代码、创建 PR 和查询 PR 使用。
- `tfs-reviewer-pat.txt`：仅“四川审核后交付”使用，应属于经授权的专用审核账号。
- `codex-api-key.txt`：一般留空；本机 Codex 会复用已有登录态。
- `aliyun-access-key-id.txt` / `aliyun-access-key-secret.txt`：OSS 上传、签名下载和到期清理使用。

这些文件已被 `.gitignore` 排除，不会上传云端。
