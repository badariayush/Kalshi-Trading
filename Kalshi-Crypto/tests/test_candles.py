from __future__ import annotations

from decimal import Decimal
import unittest

from kalshi_crypto.candles import CFBenchmarkTick, build_candles


class CandleBuilderTests(unittest.TestCase):
    def test_builds_ohlc_candles_from_cf_benchmark_ticks(self) -> None:
        ticks = (
            CFBenchmarkTick(
                index_ticker="BRTI",
                price=Decimal("100000"),
                source_timestamp_ms=1_000,
                received_timestamp_ms=1_050,
            ),
            CFBenchmarkTick(
                index_ticker="BRTI",
                price=Decimal("100500"),
                source_timestamp_ms=30_000,
                received_timestamp_ms=30_050,
            ),
            CFBenchmarkTick(
                index_ticker="BRTI",
                price=Decimal("99900"),
                source_timestamp_ms=59_000,
                received_timestamp_ms=59_050,
            ),
            CFBenchmarkTick(
                index_ticker="BRTI",
                price=Decimal("101000"),
                source_timestamp_ms=61_000,
                received_timestamp_ms=61_050,
            ),
        )

        candles = build_candles(ticks, interval_ms=60_000)

        self.assertEqual(len(candles), 2)
        self.assertEqual(candles[0].open_price, Decimal("100000"))
        self.assertEqual(candles[0].high_price, Decimal("100500"))
        self.assertEqual(candles[0].low_price, Decimal("99900"))
        self.assertEqual(candles[0].close_price, Decimal("99900"))
        self.assertEqual(candles[0].start_timestamp_ms, 0)
        self.assertEqual(candles[0].end_timestamp_ms, 60_000)
        self.assertEqual(candles[0].tick_count, 3)
        self.assertEqual(candles[1].open_price, Decimal("101000"))
        self.assertEqual(candles[1].start_timestamp_ms, 60_000)

    def test_candle_builder_rejects_mixed_indexes(self) -> None:
        ticks = (
            CFBenchmarkTick(
                index_ticker="BRTI",
                price=Decimal("100000"),
                source_timestamp_ms=1_000,
                received_timestamp_ms=1_050,
            ),
            CFBenchmarkTick(
                index_ticker="ETHUSD_RTI",
                price=Decimal("3000"),
                source_timestamp_ms=2_000,
                received_timestamp_ms=2_050,
            ),
        )

        with self.assertRaisesRegex(ValueError, "single index_ticker"):
            build_candles(ticks, interval_ms=60_000)

    def test_tick_validation_rejects_bad_prices_and_timestamps(self) -> None:
        with self.assertRaisesRegex(ValueError, "price must be positive"):
            CFBenchmarkTick(
                index_ticker="BRTI",
                price=Decimal("0"),
                source_timestamp_ms=1_000,
                received_timestamp_ms=1_000,
            )

        with self.assertRaisesRegex(ValueError, "received_timestamp_ms"):
            CFBenchmarkTick(
                index_ticker="BRTI",
                price=Decimal("100000"),
                source_timestamp_ms=2_000,
                received_timestamp_ms=1_000,
            )


if __name__ == "__main__":
    unittest.main()
