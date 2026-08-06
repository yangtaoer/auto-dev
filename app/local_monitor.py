from __future__ import annotations

import json
import logging
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .config import settings
from .live_stream import live_codex_streams


logger = logging.getLogger("autodev.monitor")


class _ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class LocalMonitorServer:
    """Loopback-only API consumed by the Windows C/S runner console."""

    def __init__(self, worker: Any, store: Any, usage_provider: Callable[[], dict[str, Any]]) -> None:
        self.worker = worker
        self.store = store
        self.usage_provider = usage_provider
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        monitor = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "AutoDevLocalMonitor"

            def log_message(self, _format: str, *_args: object) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802
                monitor._handle_get(self)

            def do_POST(self) -> None:  # noqa: N802
                monitor._handle_post(self)

        self._server = _ReusableThreadingHTTPServer(
            (settings.runner_monitor_host, settings.runner_monitor_port), Handler
        )
        self._thread = threading.Thread(target=self._server.serve_forever, name="autodev-local-monitor", daemon=True)
        self._thread.start()
        logger.info(
            "本机执行器控制台接口已启动 http://%s:%s",
            settings.runner_monitor_host,
            settings.runner_monitor_port,
        )

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread:
            self._thread.join(timeout=3)

    def _handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        try:
            if parsed.path == "/healthz":
                current_ids = self.worker.current_request_ids
                self._send(
                    handler,
                    {
                        "status": "ok",
                        "runner_id": settings.runner_id,
                        "version": settings.runner_version,
                        "state": "working" if current_ids else "idle",
                        "current_request_id": current_ids[0] if current_ids else None,
                        "current_request_ids": current_ids,
                        "active_count": len(current_ids),
                        "max_concurrency": self.worker.max_concurrency,
                        "codex_usage": self.usage_provider(),
                        "pid": os.getpid(),
                    },
                )
                return
            if parsed.path == "/api/tasks":
                query = parse_qs(parsed.query)
                limit = max(1, min(120, int(query.get("limit", ["80"])[0])))
                self._send(handler, {"tasks": self.store.list_tasks(limit)})
                return
            if parsed.path.startswith("/api/tasks/"):
                request_id = parsed.path.removeprefix("/api/tasks/")
                detail = self.store.detail(request_id)
                if not detail:
                    self._send(handler, {"detail": "任务不存在"}, HTTPStatus.NOT_FOUND)
                else:
                    self._send(handler, {"request": detail})
                return
            if parsed.path == "/api/logs":
                query = parse_qs(parsed.query)
                tail = max(20, min(2000, int(query.get("tail", ["500"])[0])))
                self._send(handler, {"text": self._tail_log(tail)})
                return
            if parsed.path == "/api/watch/poll":
                query = parse_qs(parsed.query)
                request_id = query.get("request_id", [""])[0]
                watcher_id = query.get("watcher_id", [""])[0]
                after = max(0, int(query.get("after", ["0"])[0]))
                result = live_codex_streams.poll(request_id, watcher_id, after)
                if result is None:
                    self._send(handler, {"detail": "查看会话已结束"}, HTTPStatus.GONE)
                else:
                    self._send(handler, result)
                return
            self._send(handler, {"detail": "接口不存在"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            logger.warning("本机控制台 GET 失败 path=%s error=%s", handler.path, exc)
            self._send(handler, {"detail": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        try:
            payload = self._read_json(handler)
            if parsed.path == "/api/watch/start":
                request_id = str(payload.get("request_id") or "")
                if not request_id:
                    self._send(handler, {"detail": "缺少 request_id"}, HTTPStatus.BAD_REQUEST)
                    return
                watcher_id, cursor = live_codex_streams.start(request_id)
                self._send(handler, {"watcher_id": watcher_id, "cursor": cursor, "ephemeral": True})
                return
            if parsed.path == "/api/watch/stop":
                live_codex_streams.stop(
                    str(payload.get("request_id") or ""), str(payload.get("watcher_id") or "")
                )
                self._send(handler, {"ok": True})
                return
            self._send(handler, {"detail": "接口不存在"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            logger.warning("本机控制台 POST 失败 path=%s error=%s", handler.path, exc)
            self._send(handler, {"detail": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    @staticmethod
    def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
        length = min(1024 * 1024, int(handler.headers.get("Content-Length", "0") or 0))
        if not length:
            return {}
        data = json.loads(handler.rfile.read(length).decode("utf-8"))
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _send(handler: BaseHTTPRequestHandler, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(int(status))
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    @staticmethod
    def _tail_log(line_count: int) -> str:
        path = settings.data_dir / "logs" / "runner.log"
        if not path.is_file():
            return "执行器日志尚未生成。"
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - 512 * 1024))
            text = stream.read().decode("utf-8", errors="replace")
        return "\n".join(text.splitlines()[-line_count:])
