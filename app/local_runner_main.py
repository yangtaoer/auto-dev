from __future__ import annotations

import json
import signal
import sys
import threading
import logging
import time
from logging.handlers import RotatingFileHandler

from .config import settings
from .orchestrator import Worker
from .local_monitor import LocalMonitorServer
from .project_catalog import load_project_presets
from .services.codex_runner import CodexRunner
from .store import RemoteStore


logger = logging.getLogger("autodev.runner")


def configure_logging() -> None:
    log_dir = settings.data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    file_handler = RotatingFileHandler(
        log_dir / "runner.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[file_handler, console_handler], force=True)


def main() -> None:
    configure_logging()
    logger.info("本机执行器启动 runner_id=%s cloud=%s", settings.runner_id, settings.cloud_url)
    store = RemoteStore(settings.cloud_url, settings.runner_token, settings.runner_id)
    worker = Worker(store=store)
    heartbeat_stop = threading.Event()
    last_catalog = ""
    usage_lock = threading.RLock()
    codex_usage: dict = {"available": False, "message": "正在读取 Codex 套餐信息"}
    last_usage_check = 0.0

    def refresh_codex_usage(*, force: bool = False) -> None:
        nonlocal codex_usage, last_usage_check
        now = time.monotonic()
        if not force and now - last_usage_check < 300:
            return
        last_usage_check = now
        try:
            usage = CodexRunner.read_account_usage()
            with usage_lock:
                codex_usage = usage
        except Exception as exc:
            logger.warning("Codex 套餐信息读取失败：%s", exc)
            with usage_lock:
                codex_usage = {"available": False, "message": str(exc)[:300]}

    def get_codex_usage() -> dict:
        with usage_lock:
            return dict(codex_usage)

    monitor = LocalMonitorServer(worker, store, get_codex_usage)

    def sync_project_catalog() -> None:
        nonlocal last_catalog
        projects = load_project_presets()
        fingerprint = json.dumps(projects, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if fingerprint == last_catalog:
            return
        synced = store.sync_projects(projects)
        last_catalog = fingerprint
        logger.info("本机项目目录已同步 cloud_projects=%s preset_dir=%s", len(synced), settings.project_preset_dir)

    def heartbeat_loop() -> None:
        while not heartbeat_stop.is_set():
            try:
                sync_project_catalog()
            except Exception as exc:
                logger.warning("项目目录同步失败，将继续保持心跳并稍后重试：%s", exc)
            try:
                current = worker.current_request_id
                refresh_codex_usage()
                store.heartbeat("working" if current else "idle", current, get_codex_usage())
            except Exception as exc:
                logger.warning("心跳上报失败：%s", exc)
            heartbeat_stop.wait(20)

    def cleanup_loop() -> None:
        while not heartbeat_stop.is_set():
            try:
                oss_deleted, local_deleted = store.cleanup_expired_artifacts()
                logger.info(
                    "交付物定时清理完成 oss_deleted=%s local_deleted=%s retention_days=%s",
                    oss_deleted,
                    local_deleted,
                    settings.oss_retention_days,
                )
            except Exception as exc:
                logger.warning("交付物定时清理失败：%s", exc)
            heartbeat_stop.wait(settings.oss_cleanup_interval_hours * 3600)

    def stop_runner(*_: object) -> None:
        worker.stop_event.set()

    signal.signal(signal.SIGTERM, stop_runner)
    signal.signal(signal.SIGINT, stop_runner)
    try:
        refresh_codex_usage(force=True)
        monitor.start()
        store.heartbeat("starting", codex_usage=get_codex_usage())
        try:
            sync_project_catalog()
        except Exception as exc:
            logger.warning("首次同步本机项目目录失败，将随心跳重试：%s", exc)
        heartbeat_thread = threading.Thread(target=heartbeat_loop, name="autodev-heartbeat", daemon=True)
        heartbeat_thread.start()
        if settings.oss_enabled:
            cleanup_thread = threading.Thread(target=cleanup_loop, name="autodev-oss-cleanup", daemon=True)
            cleanup_thread.start()
            logger.info(
                "OSS 交付已启用 bucket=%s prefix=%s retention_days=%s cleanup_interval_hours=%s",
                settings.oss_bucket,
                settings.oss_prefix,
                settings.oss_retention_days,
                settings.oss_cleanup_interval_hours,
            )
        worker.start()
        if worker.thread:
            worker.thread.join()
    finally:
        heartbeat_stop.set()
        try:
            store.heartbeat("stopping", codex_usage=get_codex_usage())
        except Exception:
            pass
        monitor.stop()
        store.close()
        logger.info("本机执行器已停止")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
