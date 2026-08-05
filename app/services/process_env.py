from __future__ import annotations

import base64
import os


SENSITIVE_ENV_NAMES = {
    "ALIYUN_ACCESS_KEY_ID",
    "ALIYUN_ACCESS_KEY_ID_FILE",
    "ALIYUN_ACCESS_KEY_SECRET",
    "ALIYUN_ACCESS_KEY_SECRET_FILE",
    "AUTODEV_SECRET_KEY",
    "AUTODEV_SECRET_KEY_FILE",
    "BOOTSTRAP_ADMIN_PASSWORD",
    "BOOTSTRAP_ADMIN_PASSWORD_FILE",
    "BOOTSTRAP_PM_PASSWORD",
    "BOOTSTRAP_PM_PASSWORD_FILE",
    "CODEX_API_KEY",
    "CODEX_API_KEY_FILE",
    "OPENAI_API_KEY",
    "SMTP_PASSWORD",
    "SMTP_PASSWORD_FILE",
    "TFS_PAT",
    "TFS_PAT_FILE",
    "TFS_REVIEWER_PAT",
    "TFS_REVIEWER_PAT_FILE",
}


def sanitized_process_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key not in SENSITIVE_ENV_NAMES}


def git_authenticated_env(pat: str) -> dict[str, str]:
    env = sanitized_process_env()
    token = base64.b64encode(f":{pat}".encode("ascii")).decode("ascii")
    env.update(
        {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.extraHeader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Basic {token}",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return env
