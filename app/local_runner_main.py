from __future__ import annotations

import signal
import sys
import threading
import logging
from logging.handlers import RotatingFileHandler

from .config import settings
from .orchestrator import Worker
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

    def heartbeat_loop() -> None:
        while not heartbeat_stop.is_set():
            try:
                current = worker.current_request_id
                store.heartbeat("working" if current else "idle", current)
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
        store.heartbeat("starting")
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
            store.heartbeat("stopping")
        except Exception:
            pass
        store.close()
        logger.info("本机执行器已停止")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
