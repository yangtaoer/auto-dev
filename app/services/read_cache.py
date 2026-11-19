"""Disposable cache for permission-neutral reads behind authenticated endpoints."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from functools import lru_cache
from typing import Callable

from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

from ..config import settings

logger = logging.getLogger(__name__)


class ReadCache:
    def __init__(self, url: str):
        self.client = Redis.from_url(url, socket_connect_timeout=.12, socket_timeout=.12,
                                     retry=Retry(NoBackoff(), 0), protocol=2)
        self.retry_at = 0.0

    def read(self, name: str, revision: Callable[[], object], ttl: int, loader: Callable[[], dict]) -> tuple[dict, str]:
        if self.retry_at > time.monotonic():
            return loader(), "bypass"
        key_parts = (settings.runner_version, str(settings.db_path), revision())
        digest = hashlib.sha256(json.dumps(key_parts, sort_keys=True).encode()).hexdigest()[:32]
        key = f"autodev:{name}:{digest}"
        try:
            data = self.client.get(key)
            value = json.loads(data) if data else None
            if isinstance(value, dict):
                return value, "hit"
        except (RedisError, OSError):
            self._defer()
            return loader(), "bypass"
        except ValueError:
            pass
        value = loader()
        try:
            self.client.set(key, json.dumps(value, ensure_ascii=False, separators=(",", ":")), ex=ttl)
        except (RedisError, OSError):
            self._defer()
            return value, "bypass"
        return value, "miss"

    def _defer(self):
        self.retry_at = time.monotonic() + 10
        logger.warning("Redis 暂不可用，读取已回退到 SQLite")


@lru_cache(maxsize=2)
def cache_for_url(url: str) -> ReadCache | None:
    try:
        return ReadCache(url) if url else None
    except (RedisError, ValueError):
        logger.warning("Redis 配置无效，读取使用 SQLite")
        return None


def cached_read(name: str, revision: Callable[[], object], ttl: int, loader: Callable[[], dict]) -> tuple[dict, str]:
    cache = cache_for_url(settings.redis_url)
    return cache.read(name, revision, ttl, loader) if cache else (loader(), "off")
