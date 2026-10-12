"""Bounded recovery for native imagegen, without model or provider fallback."""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass

from .codex_runner import CodexRunner

MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class Failure:
    category: str
    title: str
    details: str
    transient: bool = False
    retry_after: float = 0

    @classmethod
    def from_error(cls, error: dict | str) -> 'Failure':
        payload = error if isinstance(error, dict) else {'message': str(error)}
        signature = json.dumps(payload, ensure_ascii=False).lower()
        details = CodexRunner._failure_message(payload)
        category, title, transient = 'unknown', '这一轮未完成，输入与历史已保留。请查看详情后重试。', False
        if any(s in signature for s in ('usagelimitexceeded', 'usage_limit', 'usage limit', 'insufficient_quota', 'credit_balance_exhausted', 'spend_limit')):
            category, title = 'quota', '账户额度或用量限制已达上限，请恢复额度后重试。'
        elif any(s in signature for s in ('unauthorized', 'authentication', 'invalid_api_key')):
            category, title = 'authentication', '执行器登录状态异常，请管理员恢复登录后重试。'
        elif any(s in signature for s in ('serveroverloaded', 'server_overloaded', 'server_is_overloaded', 'at capacity')):
            category, title, transient = 'overloaded', '当前模型暂时拥堵，不是设计描述有问题，也不是额度不足。', True
        elif any(s in signature for s in ('ratelimit', 'rate_limit', 'rate limit', 'slow_down')):
            category, title, transient = 'rate_limit', '请求暂时受到限流，稍后可以重试。', True
        elif any(s in signature for s in ('streamdisconnected', 'httpconnectionfailed', 'connection reset', 'connection aborted', 'timed out', 'timeout')):
            category, title, transient = 'connection', '模型连接暂时中断，输入与历史已保留。', True
        # Only explicit native numeric hints are trusted, never arbitrary text.
        def retry_hint(value):
            if not isinstance(value, dict):return 0
            hints = [float(v) for k, v in value.items() if k.lower() in {'retryafter', 'retry_after', 'retry-after'}
                     and str(v).replace('.', '', 1).isdigit()]
            return max([0, *hints, *(retry_hint(v) for v in value.values() if isinstance(v, dict))])
        return cls(category, title, details, transient, retry_hint(payload))


class WorkshopGenerationError(RuntimeError):
    def __init__(self, error, *, thread_id='', images=(), calls=(), image_requested=False):
        self.failure = Failure.from_error(error)
        self.thread_id = thread_id
        self.images = list(images)
        self.calls = list(calls)
        self.image_requested = image_requested
        super().__init__(self.failure.details)

    @property
    def retryable(self):
        # Once a tool has started, a new conversation could duplicate image work.
        return self.failure.transient and not self.image_requested and not self.images


def generate_with_recovery(generator, job, cwd, images, *, progress, cancelled, wait, attempts):
    """Keep the same frozen model and input; callers retain the job lease heartbeat."""
    for number in range(1, MAX_ATTEMPTS + 1):
        if cancelled():raise RuntimeError('本轮已停止或超时，输入与历史已保留。')
        current = {'attempt': number, 'model': job['model_config']['model'], 'effort': job['model_config']['effort']}
        def report(message, thread_id=''):
            if thread_id:current['thread_id'] = thread_id
            progress(f'第 {number}/{MAX_ATTEMPTS} 次尝试 · {message}', thread_id)
        try:
            result = generator.generate(job, cwd, images, progress=report, cancelled=cancelled)
            attempts.append({**current, 'status': 'completed'})
            result['attempts'] = attempts
            return result
        except WorkshopGenerationError as exc:
            attempts.append({**current, 'thread_id': exc.thread_id or current.get('thread_id', ''),
                             'status': 'failed', 'category': exc.failure.category, 'details': exc.failure.details})
            if not exc.retryable or number == MAX_ATTEMPTS or cancelled():raise
            delay = max(20 * 2 ** (number - 1) + random.uniform(0, 5), exc.failure.retry_after)
            progress(f'{exc.failure.title} {delay:.0f} 秒后自动进行第 {number+1}/{MAX_ATTEMPTS} 次尝试；模型保持不变。', exc.thread_id)
            until = time.monotonic() + delay
            while time.monotonic() < until:
                if cancelled() or wait(min(1, max(0, until-time.monotonic()))):
                    raise RuntimeError('本轮已停止或超时，输入与历史已保留。')
