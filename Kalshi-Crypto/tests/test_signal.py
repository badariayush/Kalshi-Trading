from __future__ import annotations

from decimal import Decimal
import unittest

from kalshi_crypto.candles import Candle
from kalshi_crypto.market_state import MarketWindow
from kalshi_crypto.models import BookQuote
from kalshi_crypto.orderbook import NormalizedOrderBook
from kalshi_crypto.signal import SignalConfig, SignalReady, SignalSkipped, generate_signal


def _window() -> MarketWindow:
    return MarketWindow(
        market_ticker="KXBTCD-TEST",
        series_ticker="KXBTC15M",
        underlying="BTC",
        strike="102500",
        open_timestamp_ms=1_000,
        close_timestamp_ms=901_000,
        status="open",
        last_event_timestamp_ms=1_000,
    )


def _book(*, received_timestamp_ms: int = 180_000) -> NormalizedOrderBook:
    return NormalizedOrderBook(
        market_ticker="KXBTCD-TEST",
        yes_ask=BookQuote(price=Decimal("0.47"), depth=20, age_ms=100),
        no_ask=BookQuote(price=Decimal("0.55"), depth=18, age_ms=100),
        source_timestamp_ms=received_timestamp_ms - 100,
        received_timestamp_ms=received_timestamp_ms,
    )


def _candle(close: str, start_timestamp_ms: int) -> Candle:
    close_price = Decimal(close)
    return Candle(
        index_ticker="BRTI",
        start_timestamp_ms=start_timestamp_ms,
        end_timestamp_ms=start_timestamp_ms + 60_000,
        open_price=close_price - Decimal("100"),
        high_price=close_price + Decimal("50"),
        low_price=close_price - Decimal("150"),
        close_price=close_price,
        tick_count=3,
        source_timestamp_ms=start_timestamp_ms + 59_000,
        received_timestamp_ms=start_timestamp_ms + 59_100,
    )


class SignalWorkerTests(unittest.TestCase):
    def test_generates_informational_signal_feature_snapshot(self) -> None:
        candles = (
            _candle("100000", 0),
            _candle("101000", 60_000),
            _candle("103000", 120_000),
        )

        signal = generate_signal(
            window=_window(),
            candles=candles,
            orderbook=_book(),
            config=SignalConfig(
                short_ema_period=2,
                long_ema_period=3,
                max_book_age_ms=1_000,
            ),
            now_ms=180_000,
        )

        self.assertIsInstance(signal, SignalReady)
        self.assertEqual(signal.market_ticker, "KXBTCD-TEST")
        self.assertGreater(signal.probability_yes, Decimal("0.50"))
        self.assertGreater(signal.confidence, Decimal("0"))
        self.assertLessEqual(signal.confidence, Decimal("1"))
        self.assertEqual(signal.features.latest_close, Decimal("103000"))
        self.assertEqual(signal.features.kalshi_yes_ask, Decimal("0.47"))
        self.assertEqual(signal.features.kalshi_no_ask, Decimal("0.55"))
        self.assertIsNone(getattr(signal, "quantity", None))
        self.assertIsNone(getattr(signal, "order_intent", None))

    def test_skips_when_orderbook_is_stale(self) -> None:
        signal = generate_signal(
            window=_window(),
            candles=(_candle("100000", 0), _candle("101000", 60_000)),
            orderbook=_book(received_timestamp_ms=60_000),
            config=SignalConfig(max_book_age_ms=1_000),
            now_ms=180_000,
        )

        self.assertIsInstance(signal, SignalSkipped)
        self.assertEqual(signal.reason, "stale_orderbook")

    def test_skips_when_not_enough_candles_for_long_ema(self) -> None:
        signal = generate_signal(
            window=_window(),
            candles=(_candle("100000", 0),),
            orderbook=_book(),
            config=SignalConfig(long_ema_period=3),
            now_ms=180_000,
        )

        self.assertIsInstance(signal, SignalSkipped)
        self.assertEqual(signal.reason, "insufficient_candles")


if __name__ == "__main__":
    unittest.main()
