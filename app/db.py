from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Iterator

from .config import settings
from .domain import (
    ANALYSIS_PIPELINE_NAMES,
    ANALYSIS_PIPELINE_STEP_CODES,
    DEFAULT_DELIVERY_OPTIONS,
    DeliveryMode,
    PIPELINE_STEPS,
    TaskType,
)
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
CREATE TABLE IF NOT EXISTS user_emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, email)
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
    reviewer_name TEXT NOT NULL DEFAULT '',
    routing_title_keywords TEXT NOT NULL DEFAULT '[]',
    allowed_work_item_types TEXT NOT NULL DEFAULT '["用户情景"]',
    allowed_states TEXT NOT NULL DEFAULT '["已评审"]',
    repository_path TEXT NOT NULL DEFAULT '',
    repository_paths TEXT NOT NULL DEFAULT '[]',
    base_branch TEXT NOT NULL DEFAULT 'dev',
    repository_base_branches TEXT NOT NULL DEFAULT '{}',
    verification_command TEXT NOT NULL DEFAULT '',
    development_instructions TEXT NOT NULL DEFAULT '',
    repository_expectations TEXT NOT NULL DEFAULT '{}',
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
    repository_states TEXT NOT NULL DEFAULT '[]',
    codex_thread_id TEXT,
    result_summary TEXT NOT NULL DEFAULT '',
    supplement_requests TEXT NOT NULL DEFAULT '[]',
    supplement_answers TEXT NOT NULL DEFAULT '[]',
    supplement_requested_at TEXT,
    supplemented_at TEXT,
    error_message TEXT NOT NULL DEFAULT '',
    policy_snapshot TEXT NOT NULL DEFAULT '{}',
    started_at TEXT,
    completed_at TEXT,
    next_poll_at TEXT,
    email_sent_at TEXT,
    notification_emails TEXT NOT NULL DEFAULT '[]',
    delivery_options TEXT NOT NULL DEFAULT '["auto_release"]',
    joint_group_id TEXT,
    joint_project_index INTEGER NOT NULL DEFAULT 0,
    joint_project_count INTEGER NOT NULL DEFAULT 1,
    task_type TEXT NOT NULL DEFAULT 'development',
    analysis_result TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_work_item
ON delivery_requests(project_id, work_item_id)
WHERE status NOT IN ('delivered','rejected','failed','cancelled');
CREATE TABLE IF NOT EXISTS request_intakes (
    id TEXT PRIMARY KEY,
    work_item_id INTEGER NOT NULL,
    requester_id INTEGER NOT NULL REFERENCES users(id),
    runner_id TEXT NOT NULL,
    notification_emails TEXT NOT NULL DEFAULT '[]',
    delivery_options TEXT NOT NULL DEFAULT '["auto_release"]',
    status TEXT NOT NULL DEFAULT 'queued',
    result_request_id TEXT REFERENCES delivery_requests(id),
    result_request_ids TEXT NOT NULL DEFAULT '[]',
    matched_project_keys TEXT NOT NULL DEFAULT '[]',
    classification_summary TEXT NOT NULL DEFAULT '[]',
    title TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    claimed_at TEXT,
    completed_at TEXT,
    review_email_sent_at TEXT,
    email_sent_at TEXT,
    task_type TEXT NOT NULL DEFAULT 'development',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_intake_work_item
ON request_intakes(work_item_id)
WHERE status IN ('queued','claimed');
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
                    "demo-sichuan", "四川全自主示例项目", 1, 1,
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
    if "reviewer_name" not in project_columns:
        conn.execute("ALTER TABLE projects ADD COLUMN reviewer_name TEXT NOT NULL DEFAULT ''")
    if "routing_title_keywords" not in project_columns:
        conn.execute("ALTER TABLE projects ADD COLUMN routing_title_keywords TEXT NOT NULL DEFAULT '[]'")
    if "repository_paths" not in project_columns:
        conn.execute("ALTER TABLE projects ADD COLUMN repository_paths TEXT NOT NULL DEFAULT '[]'")
    if "repository_base_branches" not in project_columns:
        conn.execute("ALTER TABLE projects ADD COLUMN repository_base_branches TEXT NOT NULL DEFAULT '{}'")
    if "verification_command" not in project_columns:
        conn.execute("ALTER TABLE projects ADD COLUMN verification_command TEXT NOT NULL DEFAULT ''")
    if "development_instructions" not in project_columns:
        conn.execute("ALTER TABLE projects ADD COLUMN development_instructions TEXT NOT NULL DEFAULT ''")
    if "repository_expectations" not in project_columns:
        conn.execute("ALTER TABLE projects ADD COLUMN repository_expectations TEXT NOT NULL DEFAULT '{}'")
    request_columns = {item["name"] for item in conn.execute("PRAGMA table_info(delivery_requests)")}
    if "runner_id" not in request_columns:
        conn.execute("ALTER TABLE delivery_requests ADD COLUMN runner_id TEXT NOT NULL DEFAULT 'yangtao-pc'")
    if "notification_emails" not in request_columns:
        conn.execute("ALTER TABLE delivery_requests ADD COLUMN notification_emails TEXT NOT NULL DEFAULT '[]'")
    if "repository_states" not in request_columns:
        conn.execute("ALTER TABLE delivery_requests ADD COLUMN repository_states TEXT NOT NULL DEFAULT '[]'")
    if "supplement_requests" not in request_columns:
        conn.execute("ALTER TABLE delivery_requests ADD COLUMN supplement_requests TEXT NOT NULL DEFAULT '[]'")
    if "supplement_answers" not in request_columns:
        conn.execute("ALTER TABLE delivery_requests ADD COLUMN supplement_answers TEXT NOT NULL DEFAULT '[]'")
    if "supplement_requested_at" not in request_columns:
        conn.execute("ALTER TABLE delivery_requests ADD COLUMN supplement_requested_at TEXT")
    if "supplemented_at" not in request_columns:
        conn.execute("ALTER TABLE delivery_requests ADD COLUMN supplemented_at TEXT")
    if "delivery_options" not in request_columns:
        # NULL identifies pre-option tasks so an in-flight legacy delivery keeps its old behavior.
        conn.execute("ALTER TABLE delivery_requests ADD COLUMN delivery_options TEXT")
    if "joint_group_id" not in request_columns:
        conn.execute("ALTER TABLE delivery_requests ADD COLUMN joint_group_id TEXT")
    if "joint_project_index" not in request_columns:
        conn.execute("ALTER TABLE delivery_requests ADD COLUMN joint_project_index INTEGER NOT NULL DEFAULT 0")
    if "joint_project_count" not in request_columns:
        conn.execute("ALTER TABLE delivery_requests ADD COLUMN joint_project_count INTEGER NOT NULL DEFAULT 1")
    if "task_type" not in request_columns:
        conn.execute("ALTER TABLE delivery_requests ADD COLUMN task_type TEXT NOT NULL DEFAULT 'development'")
    if "analysis_result" not in request_columns:
        conn.execute("ALTER TABLE delivery_requests ADD COLUMN analysis_result TEXT NOT NULL DEFAULT '{}'")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_delivery_requests_joint_group ON delivery_requests(joint_group_id, joint_project_index)"
    )
    intake_columns = {item["name"] for item in conn.execute("PRAGMA table_info(request_intakes)")}
    if "delivery_options" not in intake_columns:
        conn.execute("ALTER TABLE request_intakes ADD COLUMN delivery_options TEXT")
    if "result_request_ids" not in intake_columns:
        conn.execute("ALTER TABLE request_intakes ADD COLUMN result_request_ids TEXT NOT NULL DEFAULT '[]'")
    if "matched_project_keys" not in intake_columns:
        conn.execute("ALTER TABLE request_intakes ADD COLUMN matched_project_keys TEXT NOT NULL DEFAULT '[]'")
    if "classification_summary" not in intake_columns:
        conn.execute("ALTER TABLE request_intakes ADD COLUMN classification_summary TEXT NOT NULL DEFAULT '[]'")
    if "title" not in intake_columns:
        conn.execute("ALTER TABLE request_intakes ADD COLUMN title TEXT NOT NULL DEFAULT ''")
    if "completed_at" not in intake_columns:
        conn.execute("ALTER TABLE request_intakes ADD COLUMN completed_at TEXT")
    if "review_email_sent_at" not in intake_columns:
        conn.execute("ALTER TABLE request_intakes ADD COLUMN review_email_sent_at TEXT")
    if "email_sent_at" not in intake_columns:
        conn.execute("ALTER TABLE request_intakes ADD COLUMN email_sent_at TEXT")
    if "task_type" not in intake_columns:
        conn.execute("ALTER TABLE request_intakes ADD COLUMN task_type TEXT NOT NULL DEFAULT 'development'")
    for code, name in PIPELINE_STEPS:
        conn.execute(
            """INSERT OR IGNORE INTO delivery_steps(request_id,step_code,name,status)
               SELECT id,?,?, 'pending' FROM delivery_requests""",
            (code, name),
        )
    for user in conn.execute("SELECT id,email,created_at FROM users").fetchall():
        if not conn.execute("SELECT 1 FROM user_emails WHERE user_id=? LIMIT 1", (user["id"],)).fetchone():
            conn.execute(
                "INSERT OR IGNORE INTO user_emails(user_id,email,is_primary,created_at) VALUES(?,?,1,?)",
                (user["id"], user["email"], user["created_at"]),
            )
    for request in conn.execute(
        "SELECT id,requester_id FROM delivery_requests WHERE notification_emails='[]' OR notification_emails=''"
    ).fetchall():
        emails = [
            item["email"]
            for item in conn.execute(
                "SELECT email FROM user_emails WHERE user_id=? ORDER BY is_primary DESC,id",
                (request["requester_id"],),
            ).fetchall()
        ]
        if emails:
            conn.execute(
                "UPDATE delivery_requests SET notification_emails=? WHERE id=?",
                (json.dumps(emails, ensure_ascii=False), request["id"]),
            )


def _seed_user(conn: sqlite3.Connection, username: str, display_name: str, email: str, role: str, password: str) -> None:
    existing = conn.execute("SELECT id,email,created_at FROM users WHERE username=?", (username,)).fetchone()
    if existing:
        if not conn.execute("SELECT 1 FROM user_emails WHERE user_id=? LIMIT 1", (existing["id"],)).fetchone():
            conn.execute(
                "INSERT OR IGNORE INTO user_emails(user_id,email,is_primary,created_at) VALUES(?,?,1,?)",
                (existing["id"], existing["email"], existing["created_at"]),
            )
        return
    now = utc_now()
    cursor = conn.execute(
        "INSERT INTO users(username,display_name,email,password_hash,role,created_at) VALUES(?,?,?,?,?,?)",
        (username, display_name, email, hash_password(password), role, now),
    )
    conn.execute(
        "INSERT INTO user_emails(user_id,email,is_primary,created_at) VALUES(?,?,1,?)",
        (cursor.lastrowid, email, now),
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
    result = {key: user[key] for key in ("id", "username", "display_name", "email", "role", "active")}
    result["active"] = bool(result["active"])
    configured = rows(
        "SELECT email FROM user_emails WHERE user_id=? ORDER BY is_primary DESC,id",
        (user["id"],),
    )
    result["emails"] = [item["email"] for item in configured] or [user["email"]]
    result["email"] = result["emails"][0]
    return result


def replace_user_emails(conn: sqlite3.Connection, user_id: int, emails: list[str]) -> None:
    conn.execute("DELETE FROM user_emails WHERE user_id=?", (user_id,))
    now = utc_now()
    conn.executemany(
        "INSERT INTO user_emails(user_id,email,is_primary,created_at) VALUES(?,?,?,?)",
        [(user_id, email, int(index == 0), now) for index, email in enumerate(emails)],
    )


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
        "sql_patterns", "config_patterns", "protected_patterns", "repository_paths", "routing_title_keywords",
    ):
        result[key] = json_value(result[key], [])
    result["repository_base_branches"] = json_value(result.get("repository_base_branches"), {})
    result["repository_expectations"] = json_value(result.get("repository_expectations"), {})
    result["enabled"] = bool(result["enabled"])
    result["simulation_mode"] = bool(result["simulation_mode"])
    result["allow_requirement_override"] = bool(result["allow_requirement_override"])
    return result


def create_delivery_request(
    project: dict[str, Any],
    user_id: int,
    work_item_id: int,
    mode: str,
    notification_emails: list[str],
    delivery_options: list[str] | None = None,
    *,
    joint_group_id: str | None = None,
    joint_project_index: int = 0,
    joint_project_count: int = 1,
    task_type: str = TaskType.DEVELOPMENT.value,
) -> str:
    request_id = str(uuid.uuid4())
    now = utc_now()
    snapshot = project_for_api(project)
    with transaction() as conn:
        conn.execute(
            """INSERT INTO delivery_requests(
                id,work_item_id,project_id,requester_id,runner_id,delivery_mode,status,current_step,progress,
                policy_snapshot,notification_emails,delivery_options,joint_group_id,joint_project_index,joint_project_count,
                task_type,analysis_result,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,'queued','validate',0,?,?,?,?,?,?,?,'{}',?,?)""",
            (
                request_id, work_item_id, project["id"], user_id,
                project.get("runner_id", "yangtao-pc"), mode,
                json.dumps(snapshot, ensure_ascii=False),
                json.dumps(notification_emails, ensure_ascii=False),
                json.dumps(delivery_options if delivery_options is not None else DEFAULT_DELIVERY_OPTIONS, ensure_ascii=False),
                joint_group_id, joint_project_index, joint_project_count,
                task_type, now, now,
            ),
        )
        conn.executemany(
            "INSERT INTO delivery_steps(request_id,step_code,name,status) VALUES(?,?,?,'pending')",
            [(request_id, code, name) for code, name in PIPELINE_STEPS],
        )
        conn.execute(
            "INSERT INTO delivery_events(request_id,level,event_type,message,created_at) VALUES(?,?,?,?,?)",
            (
                request_id,
                "info",
                "request.created",
                "问题分析申请已进入队列" if task_type == TaskType.ANALYSIS.value else "研发申请已进入队列",
                now,
            ),
        )
    return request_id


def create_joint_delivery_requests(
    projects: list[dict[str, Any]],
    user_id: int,
    work_item_id: int,
    notification_emails: list[str],
    delivery_options: list[str] | None,
    joint_group_id: str,
    classification: list[dict[str, Any]] | None = None,
    task_type: str = TaskType.DEVELOPMENT.value,
) -> list[str]:
    """Atomically create one independently executable child request per matched project."""
    if not projects:
        raise ValueError("联合研发至少需要一个项目")
    now = utc_now()
    total = len(projects)
    request_ids = [str(uuid.uuid4()) for _ in projects]
    classification_lookup = {
        str(item.get("project_key") or ""): item
        for item in (classification or [])
        if isinstance(item, dict)
    }
    with transaction() as conn:
        for index, (request_id, project) in enumerate(zip(request_ids, projects, strict=True), 1):
            snapshot = project_for_api(project)
            snapshot["joint_classification"] = classification_lookup.get(str(project.get("project_key") or ""), {})
            snapshot["joint_project_keys"] = [str(item.get("project_key") or "") for item in projects]
            conn.execute(
                """INSERT INTO delivery_requests(
                    id,work_item_id,project_id,requester_id,runner_id,delivery_mode,status,current_step,progress,
                    policy_snapshot,notification_emails,delivery_options,joint_group_id,joint_project_index,
                    joint_project_count,task_type,analysis_result,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,'queued','validate',0,?,?,?,?,?,?,?,'{}',?,?)""",
                (
                    request_id, work_item_id, project["id"], user_id,
                    project.get("runner_id", "yangtao-pc"), project["delivery_mode"],
                    json.dumps(snapshot, ensure_ascii=False),
                    json.dumps(notification_emails, ensure_ascii=False),
                    json.dumps(
                        delivery_options if delivery_options is not None else DEFAULT_DELIVERY_OPTIONS,
                        ensure_ascii=False,
                    ),
                    joint_group_id, index, total, task_type, now, now,
                ),
            )
            conn.executemany(
                "INSERT INTO delivery_steps(request_id,step_code,name,status) VALUES(?,?,?,'pending')",
                [(request_id, code, name) for code, name in PIPELINE_STEPS],
            )
            conn.execute(
                "INSERT INTO delivery_events(request_id,level,event_type,message,metadata,created_at) VALUES(?,?,?,?,?,?)",
                (
                    request_id,
                    "info",
                    "joint.request_created",
                    (
                        f"联合问题分析子任务 {index}/{total} 已进入队列：{project['name']}"
                        if task_type == TaskType.ANALYSIS.value
                        else f"联合研发子任务 {index}/{total} 已进入队列：{project['name']}"
                    ),
                    json.dumps({"joint_group_id": joint_group_id, "project_index": index, "project_count": total}, ensure_ascii=False),
                    now,
                ),
            )
    return request_ids


def create_request_intake(
    user_id: int,
    work_item_id: int,
    runner_id: str,
    notification_emails: list[str],
    delivery_options: list[str] | None = None,
    task_type: str = TaskType.DEVELOPMENT.value,
) -> str:
    intake_id = str(uuid.uuid4())
    now = utc_now()
    with transaction() as conn:
        conn.execute(
            """INSERT INTO request_intakes(
                id,work_item_id,requester_id,runner_id,notification_emails,delivery_options,task_type,status,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,'queued',?,?)""",
            (
                intake_id, work_item_id, user_id, runner_id,
                json.dumps(notification_emails, ensure_ascii=False),
                json.dumps(delivery_options if delivery_options is not None else DEFAULT_DELIVERY_OPTIONS, ensure_ascii=False),
                task_type, now, now,
            ),
        )
    return intake_id


def request_intake_detail(intake_id: str) -> dict[str, Any] | None:
    intake = row(
        """SELECT i.*,u.display_name requester_name
           FROM request_intakes i JOIN users u ON u.id=i.requester_id WHERE i.id=?""",
        (intake_id,),
    )
    if intake:
        intake["notification_emails"] = json_value(intake["notification_emails"], [])
        intake["delivery_options"] = (
            None if intake.get("delivery_options") is None else json_value(intake["delivery_options"], DEFAULT_DELIVERY_OPTIONS)
        )
        intake["result_request_ids"] = json_value(intake.get("result_request_ids"), [])
        intake["matched_project_keys"] = json_value(intake.get("matched_project_keys"), [])
        intake["classification_summary"] = json_value(intake.get("classification_summary"), [])
    return intake


def claim_request_intake(runner_id: str) -> dict[str, Any] | None:
    now = utc_now()
    cutoff = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    with transaction() as conn:
        conn.execute(
            """UPDATE request_intakes SET status='queued',claimed_at=NULL,updated_at=?
               WHERE runner_id=? AND status='claimed' AND claimed_at<?""",
            (now, runner_id, cutoff),
        )
        intake = conn.execute(
            """SELECT * FROM request_intakes
               WHERE runner_id=? AND status='queued' ORDER BY created_at LIMIT 1""",
            (runner_id,),
        ).fetchone()
        if not intake:
            return None
        conn.execute(
            "UPDATE request_intakes SET status='claimed',claimed_at=?,updated_at=? WHERE id=? AND status='queued'",
            (now, now, intake["id"]),
        )
        result = dict(intake)
        result["status"] = "claimed"
        result["claimed_at"] = now
        result["notification_emails"] = json_value(result["notification_emails"], [])
        return result


def add_event(request_id: str, event_type: str, message: str, *, level: str = "info", metadata: dict[str, Any] | None = None) -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT INTO delivery_events(request_id,level,event_type,message,metadata,created_at) VALUES(?,?,?,?,?,?)",
            (request_id, level, event_type, message[:2000], json.dumps(metadata or {}, ensure_ascii=False), utc_now()),
        )


def update_request(request_id: str, **fields: Any) -> None:
    if not fields:
        return
    for json_field in ("repository_states", "supplement_requests", "supplement_answers", "analysis_result"):
        if json_field in fields and not isinstance(fields[json_field], str):
            fields[json_field] = json.dumps(fields[json_field], ensure_ascii=False)
    fields["updated_at"] = utc_now()
    assignments = ",".join(f"{key}=?" for key in fields)
    with transaction() as conn:
        conn.execute(f"UPDATE delivery_requests SET {assignments} WHERE id=?", (*fields.values(), request_id))


def update_step(request_id: str, step_code: str, status: str, message: str = "") -> None:
    now = utc_now()
    with transaction() as conn:
        if status == "running":
            conn.execute(
                """UPDATE delivery_steps
                   SET status=?, message=?,
                       started_at=CASE WHEN status IN ('completed','failed','skipped') THEN ? ELSE COALESCE(started_at, ?) END,
                       finished_at=NULL
                   WHERE request_id=? AND step_code=?""",
                (status, message, now, now, request_id, step_code),
            )
        elif status in {"completed", "failed", "skipped"}:
            conn.execute(
                """UPDATE delivery_steps
                   SET status=?, message=?,
                       started_at=COALESCE(started_at, ?),
                       finished_at=COALESCE(finished_at, ?)
                   WHERE request_id=? AND step_code=?""",
                (status, message, now, now, request_id, step_code),
            )
        else:
            conn.execute(
                "UPDATE delivery_steps SET status=?, message=? WHERE request_id=? AND step_code=?",
                (status, message, request_id, step_code),
            )


def _step_duration_seconds(step: dict[str, Any], current_time: datetime) -> int | None:
    started_at = step.get("started_at")
    if not started_at:
        return None
    try:
        started = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        finished_value = step.get("finished_at")
        finished = (
            datetime.fromisoformat(str(finished_value).replace("Z", "+00:00"))
            if finished_value
            else current_time
        )
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=UTC)
        return max(0, int((finished - started).total_seconds()))
    except (TypeError, ValueError):
        return None


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
    step_order = {code: index for index, (code, _) in enumerate(PIPELINE_STEPS)}
    request["steps"] = rows("SELECT * FROM delivery_steps WHERE request_id=?", (request_id,))
    request["steps"].sort(key=lambda item: step_order.get(item["step_code"], len(step_order)))
    current_time = datetime.now(UTC)
    for step in request["steps"]:
        step["duration_seconds"] = _step_duration_seconds(step, current_time)
    request["events"] = rows("SELECT * FROM delivery_events WHERE request_id=? ORDER BY id DESC LIMIT 100", (request_id,))
    request["artifacts"] = rows(
        """SELECT * FROM delivery_artifacts
           WHERE request_id=?
             AND kind NOT IN ('report','pull_request','merge_evidence')
             AND NOT (kind='merge_screenshot' AND name LIKE '%凭证%')
           ORDER BY id""",
        (request_id,),
    )
    request["policy_snapshot"] = json_value(request["policy_snapshot"], {})
    request["repository_states"] = json_value(request.get("repository_states"), [])
    request["supplement_requests"] = json_value(request.get("supplement_requests"), [])
    request["supplement_answers"] = json_value(request.get("supplement_answers"), [])
    request["analysis_result"] = json_value(request.get("analysis_result"), {})
    request["notification_emails"] = json_value(request.get("notification_emails"), [request["requester_email"]])
    request["delivery_options"] = (
        None
        if request.get("delivery_options") is None
        else json_value(request.get("delivery_options"), DEFAULT_DELIVERY_OPTIONS)
    )
    if request.get("task_type") == TaskType.ANALYSIS.value:
        request["steps"] = [
            {
                **step,
                "name": ANALYSIS_PIPELINE_NAMES.get(step["step_code"], step["name"]),
            }
            for step in request["steps"]
            if step["step_code"] in ANALYSIS_PIPELINE_STEP_CODES
        ]
    return request
