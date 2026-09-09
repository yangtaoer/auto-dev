"""Cooperative, request-scoped cancellation (never kills the shared runner)."""
import threading
from contextlib import contextmanager


class TaskCancelled(RuntimeError):
    pass


@contextmanager
def interrupt_on_cancel(handle, is_cancelled, *, interval=3):
    done = threading.Event()

    def watch():
        while not done.wait(interval):
            try:
                if is_cancelled():
                    handle.interrupt()
                    return
            except Exception:
                # A temporary cloud outage is not permission to cancel a task.
                continue

    watcher = threading.Thread(target=watch, name='autodev-cancel', daemon=True)
    watcher.start()
    try:
        yield
    finally:
        done.set()
        watcher.join(timeout=1)
