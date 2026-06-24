from __future__ import annotations

import unittest

from kalshi_crypto.feed_health import FeedHealthMonitor


class FeedHealthTests(unittest.TestCase):
    def test_marks_source_healthy_inside_stale_threshold(self) -> None:
        monitor = FeedHealthMonitor(max_stale_ms=2_500)

        status = monitor.evaluate(
            source="kalshi_orderbook",
            source_timestamp_ms=1_000,
            received_timestamp_ms=3_400,
        )

        self.assertTrue(status.healthy)
        self.assertEqual(status.reason, "fresh")
        self.assertEqual(status.age_ms, 2_400)

    def test_marks_source_unhealthy_after_stale_threshold(self) -> None:
        monitor = FeedHealthMonitor(max_stale_ms=2_500)

        status = monitor.evaluate(
            source="kalshi_orderbook",
            source_timestamp_ms=1_000,
            received_timestamp_ms=3_501,
        )

        self.assertFalse(status.healthy)
        self.assertEqual(status.reason, "stale")
        self.assertEqual(status.age_ms, 2_501)

    def test_rejects_invalid_timestamps(self) -> None:
        monitor = FeedHealthMonitor(max_stale_ms=2_500)

        with self.assertRaisesRegex(ValueError, "received_timestamp_ms"):
            monitor.evaluate(
                source="kalshi_orderbook",
                source_timestamp_ms=2_000,
                received_timestamp_ms=1_000,
            )


if __name__ == "__main__":
    unittest.main()
