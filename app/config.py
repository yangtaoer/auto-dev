from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    configured = os.getenv("AUTODEV_ENV_FILE", "").strip()
    env_path = Path(configured).resolve() if configured else ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_secret(name: str, default: str = "") -> str:
    file_path = os.getenv(f"{name}_FILE", "").strip()
    if file_path:
        try:
            return Path(file_path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError(f"无法读取密钥文件 {name}_FILE={file_path}") from exc
    return os.getenv(name, default)


@dataclass(frozen=True)
class Settings:
    environment: str = os.getenv("AUTODEV_ENV", "development").lower()
    host: str = os.getenv("AUTODEV_HOST", "127.0.0.1")
    port: int = int(os.getenv("AUTODEV_PORT", "8765"))
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
    secret_key: str = env_secret("AUTODEV_SECRET_KEY", "local-development-secret-change-me")
    allowed_hosts: tuple[str, ...] = tuple(
        item.strip() for item in os.getenv("AUTODEV_ALLOWED_HOSTS", "127.0.0.1,localhost,testserver").split(",") if item.strip()
    )
    secure_cookies: bool = env_bool("AUTODEV_SECURE_COOKIES", False)
    data_dir: Path = (ROOT / os.getenv("AUTODEV_DATA_DIR", "data")).resolve()
    worker_enabled: bool = env_bool("AUTODEV_WORKER_ENABLED", True)
    poll_seconds: int = max(5, int(os.getenv("AUTODEV_POLL_SECONDS", "30")))
    runner_max_concurrency: int = min(5, max(1, int(os.getenv("AUTODEV_RUNNER_MAX_CONCURRENCY", "5"))))
    runner_id: str = os.getenv("AUTODEV_RUNNER_ID", "yangtao-pc").strip()
    project_preset_dir: Path = (
        ROOT / os.getenv("AUTODEV_PROJECT_PRESET_DIR", "local-runner/project-presets")
    ).resolve()
    runner_token: str = env_secret("AUTODEV_RUNNER_TOKEN")
    cloud_url: str = os.getenv("AUTODEV_CLOUD_URL", "").rstrip("/")
    runner_version: str = os.getenv("AUTODEV_RUNNER_VERSION", "1.0-Alpha.10").strip() or "1.0-Alpha.10"
    runner_monitor_host: str = os.getenv("AUTODEV_RUNNER_MONITOR_HOST", "127.0.0.1").strip()
    runner_monitor_port: int = int(os.getenv("AUTODEV_RUNNER_MONITOR_PORT", "28766"))
    max_artifact_mb: int = max(1, int(os.getenv("AUTODEV_MAX_ARTIFACT_MB", "200")))
    aliyun_access_key_id: str = env_secret("ALIYUN_ACCESS_KEY_ID")
    aliyun_access_key_secret: str = env_secret("ALIYUN_ACCESS_KEY_SECRET")
    oss_region: str = os.getenv("ALIYUN_OSS_REGION", "cn-chengdu").strip()
    oss_endpoint: str = os.getenv("ALIYUN_OSS_ENDPOINT", "").strip().rstrip("/")
    oss_bucket: str = os.getenv("ALIYUN_OSS_BUCKET", "").strip()
    oss_prefix: str = os.getenv("ALIYUN_OSS_PREFIX", "autodev").strip().strip("/")
    oss_url_expire_seconds: int = max(60, int(os.getenv("ALIYUN_OSS_URL_EXPIRE_SECONDS", "259200")))
    oss_retention_days: int = max(1, int(os.getenv("ALIYUN_OSS_RETENTION_DAYS", "3")))
    oss_cleanup_interval_hours: int = max(1, int(os.getenv("ALIYUN_OSS_CLEANUP_INTERVAL_HOURS", "72")))
    seed_demo: bool = env_bool("AUTODEV_SEED_DEMO", True)
    bootstrap_admin_password: str = env_secret("BOOTSTRAP_ADMIN_PASSWORD", "admin123")
    bootstrap_pm_password: str = env_secret("BOOTSTRAP_PM_PASSWORD", "pm123")
    tfs_pat: str = env_secret("TFS_PAT")
    tfs_reviewer_pat: str = env_secret("TFS_REVIEWER_PAT")
    tfs_reviewer_id: str = os.getenv("TFS_REVIEWER_ID", "")
    tfs_reviewer_name: str = os.getenv("TFS_REVIEWER_NAME", "四川审核人").strip()
    tfs_user_alias: str = os.getenv("TFS_USER_ALIAS", "autodev")
    tfs_license_project: str = os.getenv("TFS_LICENSE_PROJECT", "泰豪软件产品发布中心").strip()
    tfs_license_assignee: str = os.getenv("TFS_LICENSE_ASSIGNEE", r"TELLHOW\zhoudanping").strip()
    tfs_license_product: str = os.getenv("TFS_LICENSE_PRODUCT", "主配网调度运行指挥系统").strip()
    tfs_license_product_line: str = os.getenv("TFS_LICENSE_PRODUCT_LINE", "调度产品线").strip()
    tfs_license_region: str = os.getenv("TFS_LICENSE_REGION", "西南地区部").strip()
    tfs_license_purpose: str = os.getenv("TFS_LICENSE_PURPOSE", "本地自研需求测试").strip()
    tfs_pipeline_skill_script: Path = Path(
        os.getenv(
            "TFS_PIPELINE_SKILL_SCRIPT",
            str(Path.home() / ".codex" / "skills" / "tfs-run-pipeline" / "scripts" / "run_tfs_pipeline.py"),
        )
    ).resolve()
    tfs_pipeline_timeout_seconds: int = max(300, int(os.getenv("TFS_PIPELINE_TIMEOUT_SECONDS", "3600")))
    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_protocol: str = os.getenv("SMTP_PROTOCOL", "smtp").strip().lower()
    smtp_username: str = os.getenv("SMTP_USERNAME", "")
    smtp_password: str = env_secret("SMTP_PASSWORD")
    smtp_from: str = os.getenv("SMTP_FROM", "autodev@example.com")
    smtp_from_name: str = os.getenv("SMTP_FROM_NAME", "AutoDev · 自主研发交付").strip()
    task_admin_email: str = os.getenv("AUTODEV_TASK_ADMIN_EMAIL", "yangtao2@tellhow.com").strip()
    smtp_starttls: bool = env_bool("SMTP_STARTTLS", True)
    codex_model: str | None = os.getenv("CODEX_MODEL") or None
    codex_api_key: str = env_secret("CODEX_API_KEY")

    @property
    def oss_enabled(self) -> bool:
        return bool(
            self.aliyun_access_key_id
            and self.aliyun_access_key_secret
            and self.oss_endpoint
            and self.oss_bucket
        )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "autodev.db"

    @property
    def worktree_dir(self) -> Path:
        return self.data_dir / "worktrees"

    @property
    def delivery_dir(self) -> Path:
        return self.data_dir / "deliveries"

    @property
    def repository_dir(self) -> Path:
        return self.data_dir / "repositories"


settings = Settings()
