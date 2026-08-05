from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Iterator

from .config import settings
from .domain import DeliveryMode, PIPELINE_STEPS
from .security import hash_password


_write_lock = threading.RLock()


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def connect() -> sqlite3.Connection:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    with _write_lock:
        conn = connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    email TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'pm')),
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_key TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    simulation_mode INTEGER NOT NULL DEFAULT 1,
    delivery_mode TEXT NOT NULL,
    allow_requirement_override INTEGER NOT NULL DEFAULT 0,
    tfs_collection_url TEXT NOT NULL,
    tfs_project TEXT NOT NULL,
    tfs_area_path TEXT NOT NULL DEFAULT '',
    allowed_work_item_types TEXT NOT NULL DEFAULT '["用户情景"]',
    allowed_states TEXT NOT NULL DEFAULT '["已评审"]',
    repository_path TEXT NOT NULL DEFAULT '',
    base_branch TEXT NOT NULL DEFAULT 'dev',
    build_command TEXT NOT NULL DEFAULT '',
    package_patterns TEXT NOT NULL DEFAULT '[]',
    sql_patterns TEXT NOT NULL DEFAULT '["**/*.sql"]',
    config_patterns TEXT NOT NULL DEFAULT '["**/*.yml","**/*.yaml","**/*.properties","**/*.xml"]',
    protected_patterns TEXT NOT NULL DEFAULT '["**/common/**","**/shared/**","**/production/**"]',
    notification_cc TEXT NOT NULL DEFAULT '',
    runner_id TEXT NOT NULL DEFAULT 'yangtao-pc',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS delivery_requests (
    id TEXT PRIMARY KEY,
    work_item_id INTEGER NOT NULL,
    work_item_revision INTEGER,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    requester_id INTEGER NOT NULL REFERENCES users(id),
    runner_id TEXT NOT NULL DEFAULT 'yangtao-pc',
    delivery_mode TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    requirement_summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    current_step TEXT NOT NULL DEFAULT '',
    progress INTEGER NOT NULL DEFAULT 0,
    branch_name TEXT,
    base_commit TEXT,
    commit_hash TEXT,
    pr_id INTEGER,
    pr_url TEXT,
    merge_commit TEXT,
    codex_thread_id TEXT,
    result_summary TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    policy_snapshot TEXT NOT NULL DEFAULT '{}',
    started_at TEXT,
    completed_at TEXT,
    next_poll_at TEXT,
    email_sent_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_work_item
ON delivery_requests(project_id, work_item_id)
WHERE status NOT IN ('delivered','rejected','failed','cancelled');
CREATE TABLE IF NOT EXISTS delivery_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL REFERENCES delivery_requests(id) ON DELETE CASCADE,
    step_code TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    message TEXT NOT NULL DEFAULT '',
    started_at TEXT,
    finished_at TEXT,
    UNIQUE(request_id, step_code)
);
CREATE TABLE IF NOT EXISTS delivery_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL REFERENCES delivery_requests(id) ON DELETE CASCADE,
    level TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS delivery_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL REFERENCES delivery_requests(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    local_path TEXT NOT NULL DEFAULT '',
    external_url TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER REFERENCES users(id),
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runners (
    runner_id TEXT PRIMARY KEY,
    hostname TEXT NOT NULL DEFAULT '',
    version TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'idle',
    current_request_id TEXT,
    last_seen_at TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}'
);
"""


def init_db() -> None:
    if settings.environment == "production" and settings.bootstrap_admin_password in {"", "admin123"}:
        raise RuntimeError("生产环境必须通过 BOOTSTRAP_ADMIN_PASSWORD_FILE 配置强管理员密码")
    settings.worktree_dir.mkdir(parents=True, exist_ok=True)
    settings.delivery_dir.mkdir(parents=True, exist_ok=True)
    settings.repository_dir.mkdir(parents=True, exist_ok=True)
    with transaction() as conn:
        conn.executescript(SCHEMA)
        _migrate_schema(conn)
        _seed_user(conn, "admin", "系统管理员", "admin@example.com", "admin", settings.bootstrap_admin_password)
        if settings.seed_demo:
            _seed_user(conn, "pm", "项目经理", "pm@example.com", "pm", settings.bootstrap_pm_password)
        exists = conn.execute("SELECT 1 FROM projects LIMIT 1").fetchone()
        if not exists and settings.seed_demo:
            now = utc_now()
            conn.execute(
                """INSERT INTO projects (
                    project_key,name,enabled,simulation_mode,delivery_mode,allow_requirement_override,
                    tfs_collection_url,tfs_project,tfs_area_path,repository_path,base_branch,build_command,
                    package_patterns,sql_patterns,config_patterns,protected_patterns,notification_cc,runner_id,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "demo-sichuan", "四川全自助示例项目", 1, 1,
                    DeliveryMode.PRODUCT_MANUAL_REVIEW.value, 0,
                    "http://dev.tellhowsoft.com/DefaultCollection", "XiNanArea-New",
                    "XiNanArea-New\\四川省区团队", "", "dev", "",
                    '["dist/**/*","target/*.jar"]', '["**/*.sql"]',
                    '["**/*.yml","**/*.yaml","**/*.properties","**/*.xml"]',
                    '["**/common/**","**/shared/**","**/production/**"]', "", "yangtao-pc", now, now,
                ),
            )


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Apply additive migrations so an existing local database remains usable."""
    project_columns = {item["name"] for item in conn.execute("PRAGMA table_info(projects)")}
    if "runner_id" not in project_columns:
        conn.execute("ALTER TABLE projects ADD COLUMN runner_id TEXT NOT NULL DEFAULT 'yangtao-pc'")
    if "allowed_work_item_types" not in project_columns:
        conn.execute("ALTER TABLE projects ADD COLUMN allowed_work_item_types TEXT NOT NULL DEFAULT '[\"用户情景\"]'")
    if "allowed_states" not in project_columns:
        conn.execute("ALTER TABLE projects ADD COLUMN allowed_states TEXT NOT NULL DEFAULT '[\"已评审\"]'")
    request_columns = {item["name"] for item in conn.execute("PRAGMA table_info(delivery_requests)")}
    if "runner_id" not in request_columns:
        conn.execute("ALTER TABLE delivery_requests ADD COLUMN runner_id TEXT NOT NULL DEFAULT 'yangtao-pc'")


def _seed_user(conn: sqlite3.Connection, username: str, display_name: str, email: str, role: str, password: str) -> None:
    if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        return
    conn.execute(
        "INSERT INTO users(username,display_name,email,password_hash,role,created_at) VALUES(?,?,?,?,?,?)",
        (username, display_name, email, hash_password(password), role, utc_now()),
    )


def rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    conn = connect()
    try:
        return [dict(item) for item in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def row(query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    conn = connect()
    try:
        result = conn.execute(query, params).fetchone()
        return dict(result) if result else None
    finally:
        conn.close()


def json_value(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except json.JSONDecodeError:
        return fallback


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {key: user[key] for key in ("id", "username", "display_name", "email", "role", "active")}


def create_session(user_id: int) -> tuple[str, str]:
    from .security import new_token

    token = new_token()
    token_hash = __import__("hashlib").sha256(token.encode()).hexdigest()
    expires = (datetime.now(UTC) + timedelta(hours=12)).isoformat()
    with transaction() as conn:
        conn.execute(
            "INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)",
            (token_hash, user_id, expires, utc_now()),
        )
    return token, expires


def get_session_user(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    token_hash = __import__("hashlib").sha256(token.encode()).hexdigest()
    user = row(
        """SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
           WHERE s.token_hash=? AND s.expires_at>? AND u.active=1""",
        (token_hash, utc_now()),
    )
    return user


def delete_session(token: str | None) -> None:
    if not token:
        return
    token_hash = __import__("hashlib").sha256(token.encode()).hexdigest()
    with transaction() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))


def project_for_api(project: dict[str, Any]) -> dict[str, Any]:
    result = dict(project)
    for key in (
        "allowed_work_item_types", "allowed_states", "package_patterns",
        "sql_patterns", "config_patterns", "protected_patterns",
    ):
        result[key] = json_value(result[key], [])
    result["enabled"] = bool(result["enabled"])
    result["simulation_mode"] = bool(result["simulation_mode"])
    result["allow_requirement_override"] = bool(result["allow_requirement_override"])
    return result


def create_delivery_request(project: dict[str, Any], user_id: int, work_item_id: int, mode: str) -> str:
    request_id = str(uuid.uuid4())
    now = utc_now()
    snapshot = project_for_api(project)
    with transaction() as conn:
        conn.execute(
            """INSERT INTO delivery_requests(
                id,work_item_id,project_id,requester_id,runner_id,delivery_mode,status,current_step,progress,
                policy_snapshot,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,'queued','validate',0,?,?,?)""",
            (request_id, work_item_id, project["id"], user_id, project.get("runner_id", "yangtao-pc"), mode, json.dumps(snapshot, ensure_ascii=False), now, now),
        )
        conn.executemany(
            "INSERT INTO delivery_steps(request_id,step_code,name,status) VALUES(?,?,?,'pending')",
            [(request_id, code, name) for code, name in PIPELINE_STEPS],
        )
        conn.execute(
            "INSERT INTO delivery_events(request_id,level,event_type,message,created_at) VALUES(?,?,?,?,?)",
            (request_id, "info", "request.created", "研发申请已进入队列", now),
        )
    return request_id


def add_event(request_id: str, event_type: str, message: str, *, level: str = "info", metadata: dict[str, Any] | None = None) -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT INTO delivery_events(request_id,level,event_type,message,metadata,created_at) VALUES(?,?,?,?,?,?)",
            (request_id, level, event_type, message[:2000], json.dumps(metadata or {}, ensure_ascii=False), utc_now()),
        )


def update_request(request_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = utc_now()
    assignments = ",".join(f"{key}=?" for key in fields)
    with transaction() as conn:
        conn.execute(f"UPDATE delivery_requests SET {assignments} WHERE id=?", (*fields.values(), request_id))


def update_step(request_id: str, step_code: str, status: str, message: str = "") -> None:
    now = utc_now()
    fields = {"status": status, "message": message}
    if status == "running":
        fields["started_at"] = now
    if status in {"completed", "failed", "skipped"}:
        fields["finished_at"] = now
    assignments = ",".join(f"{key}=?" for key in fields)
    with transaction() as conn:
        conn.execute(
            f"UPDATE delivery_steps SET {assignments} WHERE request_id=? AND step_code=?",
            (*fields.values(), request_id, step_code),
        )


def add_artifact(request_id: str, kind: str, name: str, local_path: str = "", external_url: str = "") -> int:
    size = 0
    if local_path:
        try:
            size = __import__("pathlib").Path(local_path).stat().st_size
        except OSError:
            pass
    with transaction() as conn:
        cursor = conn.execute(
            """INSERT INTO delivery_artifacts(request_id,kind,name,local_path,external_url,size_bytes,created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (request_id, kind, name, local_path, external_url, size, utc_now()),
        )
        return int(cursor.lastrowid)


def request_detail(request_id: str) -> dict[str, Any] | None:
    request = row(
        """SELECT r.*, p.name project_name, p.project_key, u.display_name requester_name, u.email requester_email
           FROM delivery_requests r JOIN projects p ON p.id=r.project_id JOIN users u ON u.id=r.requester_id
           WHERE r.id=?""",
        (request_id,),
    )
    if not request:
        return None
    request["steps"] = rows("SELECT * FROM delivery_steps WHERE request_id=? ORDER BY id", (request_id,))
    request["events"] = rows("SELECT * FROM delivery_events WHERE request_id=? ORDER BY id DESC LIMIT 100", (request_id,))
    request["artifacts"] = rows("SELECT * FROM delivery_artifacts WHERE request_id=? ORDER BY id", (request_id,))
    request["policy_snapshot"] = json_value(request["policy_snapshot"], {})
    return request
