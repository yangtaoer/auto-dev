from __future__ import annotations

import signal
import sys

from .db import init_db
from .orchestrator import worker


def main() -> None:
    init_db()

    def stop_worker(*_: object) -> None:
        worker.stop_event.set()

    signal.signal(signal.SIGTERM, stop_worker)
    signal.signal(signal.SIGINT, stop_worker)
    worker.start()
    worker.join()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
