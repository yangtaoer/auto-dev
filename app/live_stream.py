from __future__ import annotations

import queue
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Any


class LiveCodexHub:
    """Process-local, watcher-gated Codex output bus.

    Events only exist while at least one viewer is actively polling. This keeps the
    detailed Codex session out of the database and out of delivery records.
    """

    def __init__(self, *, watcher_ttl: float = 15.0, max_events: int = 800) -> None:
        self.watcher_ttl = watcher_ttl
        self.max_events = max_events
        self._lock = threading.RLock()
        self._watchers: dict[str, dict[str, float]] = defaultdict(dict)
        self._events: dict[str, deque[dict[str, Any]]] = {}
        self._sequence: dict[str, int] = defaultdict(int)

    def start(self, request_id: str) -> tuple[str, int]:
        with self._lock:
            self._prune(request_id)
            if not self._watchers.get(request_id):
                self._events.pop(request_id, None)
                self._sequence[request_id] = 0
            watcher_id = uuid.uuid4().hex
            self._watchers[request_id][watcher_id] = time.monotonic() + self.watcher_ttl
            return watcher_id, self._sequence[request_id]

    def active(self, request_id: str) -> bool:
        with self._lock:
            self._prune(request_id)
            return bool(self._watchers.get(request_id))

    def publish(self, request_id: str, event: dict[str, Any]) -> bool:
        with self._lock:
            self._prune(request_id)
            if not self._watchers.get(request_id):
                return False
            self._sequence[request_id] += 1
            item = {
                "seq": self._sequence[request_id],
                "at": datetime.now(UTC).isoformat(),
                "kind": str(event.get("kind") or "status")[:40],
                "content": str(event.get("content") or "")[:12000],
                "group": str(event.get("group") or "")[:160],
                "delta": bool(event.get("delta")),
            }
            events = self._events.setdefault(request_id, deque(maxlen=self.max_events))
            events.append(item)
            return True

    def publish_many(self, request_id: str, events: list[dict[str, Any]]) -> int:
        return sum(1 for event in events if self.publish(request_id, event))

    def poll(self, request_id: str, watcher_id: str, after: int = 0) -> dict[str, Any] | None:
        with self._lock:
            self._prune(request_id)
            watchers = self._watchers.get(request_id)
            if not watchers or watcher_id not in watchers:
                return None
            watchers[watcher_id] = time.monotonic() + self.watcher_ttl
            events = [item.copy() for item in self._events.get(request_id, ()) if item["seq"] > after]
            return {"events": events, "cursor": self._sequence[request_id], "active": True}

    def stop(self, request_id: str, watcher_id: str) -> None:
        with self._lock:
            watchers = self._watchers.get(request_id)
            if watchers:
                watchers.pop(watcher_id, None)
            self._prune(request_id)

    def _prune(self, request_id: str) -> None:
        now = time.monotonic()
        watchers = self._watchers.get(request_id)
        if watchers:
            expired = [watcher_id for watcher_id, deadline in watchers.items() if deadline <= now]
            for watcher_id in expired:
                watchers.pop(watcher_id, None)
        if not watchers:
            self._watchers.pop(request_id, None)
            self._events.pop(request_id, None)


live_codex_streams = LiveCodexHub()


class LiveCodexPublisher:
    """Fans live events to local viewers and, for remote runners, cloud viewers."""

    def __init__(self, request_id: str, store: Any) -> None:
        self.request_id = request_id
        self.store = store
        self._closed = threading.Event()
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1500)
        self._thread: threading.Thread | None = None
        if getattr(store, "remote", False):
            self._thread = threading.Thread(target=self._remote_loop, name=f"codex-live-{request_id[:8]}", daemon=True)
            self._thread.start()

    def emit(self, event: dict[str, Any]) -> None:
        live_codex_streams.publish(self.request_id, event)
        if not self._thread:
            return
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            # Live detail is deliberately lossy and non-persistent.
            pass

    def close(self) -> None:
        self._closed.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _remote_loop(self) -> None:
        known_active = False
        next_active_check = 0.0
        while not self._closed.is_set() or not self._queue.empty():
            batch: list[dict[str, Any]] = []
            try:
                batch.append(self._queue.get(timeout=0.2))
            except queue.Empty:
                continue
            deadline = time.monotonic() + 0.12
            while len(batch) < 80 and time.monotonic() < deadline:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            now = time.monotonic()
            if now >= next_active_check:
                try:
                    known_active = bool(self.store.codex_watch_active(self.request_id))
                except Exception:
                    known_active = False
                next_active_check = now + (0.7 if known_active else 1.0)
            if known_active:
                try:
                    self.store.publish_codex_events(self.request_id, batch)
                except Exception:
                    known_active = False
                    next_active_check = 0.0
