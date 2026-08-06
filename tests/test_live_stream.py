from __future__ import annotations

import time
import unittest

from app.live_stream import LiveCodexHub


class LiveCodexHubTests(unittest.TestCase):
    def test_events_exist_only_while_a_watcher_is_active(self) -> None:
        hub = LiveCodexHub(watcher_ttl=0.04, max_events=5)
        self.assertFalse(hub.publish("run-1", {"kind": "assistant", "content": "before"}))
        watcher_id, cursor = hub.start("run-1")
        self.assertTrue(hub.publish("run-1", {"kind": "assistant", "content": "during", "delta": True}))
        result = hub.poll("run-1", watcher_id, cursor)
        self.assertEqual(result["events"][0]["content"], "during")
        hub.stop("run-1", watcher_id)
        self.assertFalse(hub.active("run-1"))
        self.assertFalse(hub.publish("run-1", {"kind": "assistant", "content": "after"}))

    def test_abandoned_watcher_expires_and_clears_buffer(self) -> None:
        hub = LiveCodexHub(watcher_ttl=0.01)
        watcher_id, _ = hub.start("run-2")
        hub.publish("run-2", {"kind": "command", "content": "temporary"})
        time.sleep(0.02)
        self.assertIsNone(hub.poll("run-2", watcher_id, 0))
        new_watcher, cursor = hub.start("run-2")
        self.assertEqual(cursor, 0)
        self.assertEqual(hub.poll("run-2", new_watcher, 0)["events"], [])


if __name__ == "__main__":
    unittest.main()
