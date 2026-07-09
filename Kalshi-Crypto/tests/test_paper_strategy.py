from __future__ import annotations

from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kalshi_crypto.config import AppConfig
from kalshi_crypto.live_collectors import run_live_data_audit
from kalshi_crypto.live_collectors import LiveMessageRecord
from kalshi_crypto.paper_strategy import (
    PaperFeedRecord,
    PaperMarket,
    PaperRealtimeState,
    advance_realtime_paper,
    evaluate_live_paper_strategy,
)
from kalshi_crypto.storage import SQLiteAuditStore


MARKET_TICKER = "KXBTC15M-TEST"
START_MS = 1_700_000_000_000


class LivePaperStrategyTests(unittest.TestCase):
    def test_falling_btc_path_enters_no_instead_of_hardcoded_yes(self) -> None:
        result = evaluate_live_paper_strategy(
            records=_records(prices=_falling_prices()),
            market=_market(strike="100"),
            config=_config(partial_hedge_enabled=False),
        )

        self.assertEqual(len(result.orders), 1)
        self.assertEqual(result.orders[0].side, "no")
        self.assertEqual(result.orders[0].reason, "directional_entry")
        self.assertEqual(result.orders[0].leg_index, 1)

    def test_rising_btc_path_enters_yes(self) -> None:
        result = evaluate_live_paper_strategy(
            records=_records(prices=_rising_prices()),
            market=_market(strike="100"),
            config=_config(partial_hedge_enabled=False),
        )

        self.assertEqual(len(result.orders), 1)
        self.assertEqual(result.orders[0].side, "yes")
        self.assertEqual(result.orders[0].reason, "directional_entry")

    def test_second_leg_is_opposite_and_market_is_capped_at_two_legs(self) -> None:
        result = evaluate_live_paper_strategy(
            records=_records(
                prices=_falling_prices(),
                yes_asks_after_entry=("0.45", "0.51", "0.55", "0.60"),
            ),
            market=_market(strike="100"),
            config=_config(partial_hedge_enabled=True),
        )

        self.assertEqual(len(result.orders), 2)
        self.assertEqual([order.side for order in result.orders], ["no", "yes"])
        self.assertEqual(result.orders[1].reason, "partial_hedge")
        self.assertEqual(result.orders[1].leg_index, 2)
        self.assertNotEqual(result.orders[0].side, result.orders[1].side)

    def test_yes_first_leg_can_only_add_no_as_second_leg(self) -> None:
        result = evaluate_live_paper_strategy(
            records=_records(
                prices=_rising_prices(),
                base_yes_ask="0.56",
                yes_asks_after_entry=("0.56", "0.50", "0.44", "0.40"),
            ),
            market=_market(strike="100"),
            config=_config(partial_hedge_enabled=True),
        )

        self.assertEqual([order.side for order in result.orders], ["yes", "no"])
        self.assertEqual(result.orders[1].reason, "partial_hedge")
        self.assertEqual(len(result.orders), 2)

    def test_realtime_state_emits_directional_entry_before_capture_ends(self) -> None:
        records = _records(prices=_falling_prices())
        state = PaperRealtimeState.empty()
        emitted = []
        emission_record_index = None

        for index, record in enumerate(records):
            step = advance_realtime_paper(
                state=state,
                record=record,
                market=_market(strike="100"),
                config=_config(partial_hedge_enabled=False),
            )
            state = step.state
            if step.orders:
                emitted.extend(step.orders)
                emission_record_index = index
                break

        self.assertEqual(emitted[0].side, "no")
        self.assertLess(emission_record_index, len(records) - 1)
        self.assertEqual(emitted[0].price, Decimal("0.565"))

    def test_take_profit_exit_uses_live_bid_slippage_and_two_sided_fees(self) -> None:
        result = evaluate_live_paper_strategy(
            records=_records(
                prices=_rising_prices(),
                quote_pairs_after_entry=(("0.62", "0.60"),),
            ),
            market=_market(strike="100"),
            config=_config(partial_hedge_enabled=False),
        )

        self.assertEqual(len(result.orders), 1)
        self.assertIsNotNone(result.exit)
        self.assertEqual(result.exit.reason, "take_profit")
        self.assertEqual(result.exit.price, Decimal("0.595"))
        self.assertEqual(result.exit.realized_pnl, Decimal("0.10"))

    def test_realtime_state_emits_each_order_and_exit_only_once(self) -> None:
        records = _records(
            prices=_rising_prices(),
            quote_pairs_after_entry=(("0.62", "0.60"),),
        )
        state = PaperRealtimeState.empty()
        emitted_orders = []
        emitted_exits = []

        for record in (*records, *records[-4:]):
            step = advance_realtime_paper(
                state=state,
                record=record,
                market=_market(strike="100"),
                config=_config(partial_hedge_enabled=False),
            )
            state = step.state
            emitted_orders.extend(step.orders)
            if step.exit is not None:
                emitted_exits.append(step.exit)

        self.assertEqual(len(emitted_orders), 1)
        self.assertEqual(len(emitted_exits), 1)

    def test_late_directional_entry_is_blocked_but_management_is_separate(self) -> None:
        market = PaperMarket(
            ticker=MARKET_TICKER,
            series_ticker="KXBTC15M",
            underlying="BTC",
            strike=Decimal("100"),
            open_time_ms=START_MS - 400_000,
            close_time_ms=START_MS + 500_000,
        )

        result = evaluate_live_paper_strategy(
            records=_records(prices=_rising_prices()),
            market=market,
            config=_config(partial_hedge_enabled=False),
        )

        self.assertEqual(result.orders, ())
        self.assertIsNone(result.exit)

    def test_does_not_trade_without_enough_live_candles(self) -> None:
        result = evaluate_live_paper_strategy(
            records=_records(prices=(Decimal("100"), Decimal("99"))),
            market=_market(strike="100"),
            config=_config(partial_hedge_enabled=True),
        )

        self.assertEqual(result.orders, ())
        self.assertEqual(result.skip_reason, "insufficient_live_signal_data")

    def test_does_not_enter_when_latest_coinbase_price_is_stale(self) -> None:
        records = tuple(
            record
            for record in _records(prices=_falling_prices())
            if (
                record.source == "coinbase"
                and record.received_timestamp_ms <= START_MS + 39_000
            )
            or (
                record.source == "kalshi"
                and record.received_timestamp_ms >= START_MS + 45_000
            )
        )

        result = evaluate_live_paper_strategy(
            records=records,
            market=_market(strike="100"),
            config=_config(partial_hedge_enabled=False),
        )

        self.assertEqual(result.orders, ())

    def test_live_audit_prints_and_records_strategy_selected_no_order(self) -> None:
        records = _records(prices=_falling_prices())
        with tempfile.TemporaryDirectory() as tmpdir:
            input_file = Path(tmpdir) / "live.jsonl"
            audit_db = Path(tmpdir) / "audit.sqlite3"
            input_file.write_text(
                "\n".join(
                    json.dumps(
                        {
                            "source": record.source,
                            "message": record.message,
                            "received_timestamp_ms": record.received_timestamp_ms,
                        }
                    )
                    for record in records
                )
                + "\n",
                encoding="utf-8",
            )
            stdout = StringIO()

            summary = run_live_data_audit(
                config=_config(partial_hedge_enabled=False),
                audit_db=audit_db,
                max_seconds=60,
                kalshi_market_tickers=(MARKET_TICKER,),
                input_file=input_file,
                stdout=stdout,
                paper_market=_market(strike="100"),
            )
            stored = SQLiteAuditStore(audit_db).read_all()

        order_events = [
            event for event in stored if event["event_type"] == "SimulatedOrderPlaced"
        ]
        self.assertEqual(summary.simulated_orders, 1)
        self.assertEqual(order_events[0]["payload"]["side"], "no")
        self.assertEqual(order_events[0]["payload"]["leg_index"], 1)
        self.assertIn("side=no", stdout.getvalue())
        self.assertIn("order_submission=disabled", stdout.getvalue())

    def test_live_audit_records_realtime_style_take_profit_pnl(self) -> None:
        records = _records(
            prices=_rising_prices(),
            quote_pairs_after_entry=(("0.62", "0.60"),),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            input_file = Path(tmpdir) / "live.jsonl"
            audit_db = Path(tmpdir) / "audit.sqlite3"
            _write_records(input_file, records)
            stdout = StringIO()

            summary = run_live_data_audit(
                config=_config(partial_hedge_enabled=False),
                audit_db=audit_db,
                max_seconds=60,
                input_file=input_file,
                stdout=stdout,
                paper_market=_market(strike="100"),
            )

        self.assertEqual(summary.simulated_positions_closed, 1)
        self.assertEqual(summary.simulated_realized_pnl, Decimal("0.10"))
        self.assertIn("simulated_exit_filled=", stdout.getvalue())

    def test_network_callback_persists_realtime_entry_and_exit_once(self) -> None:
        live_records = tuple(
            LiveMessageRecord(
                source=record.source,
                message=record.message,
                received_timestamp_ms=record.received_timestamp_ms,
            )
            for record in _records(
                prices=_rising_prices(),
                quote_pairs_after_entry=(("0.62", "0.60"),),
            )
        )

        async def collect_records(**kwargs: object) -> list[LiveMessageRecord]:
            on_record = kwargs["on_record"]
            for record in live_records:
                on_record(record)
            return list(live_records)

        with tempfile.TemporaryDirectory() as tmpdir:
            audit_db = Path(tmpdir) / "audit.sqlite3"
            stdout = StringIO()
            with patch(
                "kalshi_crypto.live_collectors._collect_network_messages",
                side_effect=collect_records,
            ):
                summary = run_live_data_audit(
                    config=_config(partial_hedge_enabled=False),
                    audit_db=audit_db,
                    max_seconds=60,
                    kalshi_market_tickers=(MARKET_TICKER,),
                    stdout=stdout,
                    paper_market=_market(strike="100"),
                )
            stored = SQLiteAuditStore(audit_db).read_all()

        self.assertEqual(summary.network, "attempted")
        self.assertEqual(summary.simulated_orders, 1)
        self.assertEqual(summary.simulated_positions_closed, 1)
        self.assertEqual(
            sum(event["event_type"] == "SimulatedOrderPlaced" for event in stored),
            1,
        )
        self.assertEqual(
            sum(event["event_type"] == "PositionClosed" for event in stored),
            1,
        )
        self.assertEqual(stdout.getvalue().count("simulated_order_placed="), 1)
        self.assertEqual(stdout.getvalue().count("simulated_exit_filled="), 1)


def _config(*, partial_hedge_enabled: bool) -> AppConfig:
    return AppConfig.from_mapping(
        {
            "runtime": {"mode": "live_data"},
            "trade_management": {
                "partial_hedge_enabled": partial_hedge_enabled,
                "partial_hedge_opposing_ask_trigger": "0.50",
                "partial_hedge_max_loss_pct_of_original_risk": "0.50",
                "partial_hedge_max_contracts_pct_of_original_size": "1.00",
                "min_arb_margin": "0.02",
                "slippage_buffer": "0.005",
                "max_entry_book_age_ms": 1500,
                "max_true_arb_book_age_ms": 750,
                "max_partial_hedge_book_age_ms": 750,
                "min_depth_contracts": 1,
            },
            "paper_strategy": {
                "underlying_product_id": "BTC-USD",
                "candle_interval_seconds": 5,
                "short_ema_period": 3,
                "long_ema_period": 8,
                "min_probability_edge": "0.01",
                "min_confidence": "0.01",
                "quantity": 1,
            },
        }
    )


def _market(*, strike: str) -> PaperMarket:
    return PaperMarket(
        ticker=MARKET_TICKER,
        series_ticker="KXBTC15M",
        underlying="BTC",
        strike=Decimal(strike),
        open_time_ms=START_MS,
        close_time_ms=START_MS + 900_000,
    )


def _falling_prices() -> tuple[Decimal, ...]:
    return tuple(Decimal("110") - Decimal(index) / Decimal("2") for index in range(50))


def _rising_prices() -> tuple[Decimal, ...]:
    return tuple(Decimal("90") + Decimal(index) / Decimal("2") for index in range(50))


def _records(
    *,
    prices: tuple[Decimal, ...],
    base_yes_ask: str = "0.45",
    yes_asks_after_entry: tuple[str, ...] = (),
    quote_pairs_after_entry: tuple[tuple[str, str], ...] = (),
) -> tuple[PaperFeedRecord, ...]:
    records: list[PaperFeedRecord] = []
    for index, price in enumerate(prices):
        timestamp_ms = START_MS + (index * 1_000)
        records.append(_coinbase_record(timestamp_ms=timestamp_ms, price=price))

        if index >= 41 and quote_pairs_after_entry:
            offset = min(index - 41, len(quote_pairs_after_entry) - 1)
            yes_ask = Decimal(quote_pairs_after_entry[offset][0])
            yes_bid = Decimal(quote_pairs_after_entry[offset][1])
        elif index < 41 or not yes_asks_after_entry:
            yes_ask = Decimal(base_yes_ask)
            yes_bid = yes_ask - Decimal("0.01")
        else:
            offset = min(index - 41, len(yes_asks_after_entry) - 1)
            yes_ask = Decimal(yes_asks_after_entry[offset])
            yes_bid = yes_ask - Decimal("0.01")
        records.append(
            _kalshi_record(
                timestamp_ms=timestamp_ms,
                yes_ask=yes_ask,
                yes_bid=yes_bid,
            )
        )
    return tuple(records)


def _coinbase_record(*, timestamp_ms: int, price: Decimal) -> PaperFeedRecord:
    return PaperFeedRecord(
        source="coinbase",
        received_timestamp_ms=timestamp_ms,
        message={
            "channel": "ticker",
            "timestamp_ms": timestamp_ms,
            "events": [
                {
                    "type": "update",
                    "tickers": [
                        {
                            "product_id": "BTC-USD",
                            "price": str(price),
                        }
                    ],
                }
            ],
        },
    )


def _write_records(path: Path, records: tuple[PaperFeedRecord, ...]) -> None:
    path.write_text(
        "\n".join(
            json.dumps(
                {
                    "source": record.source,
                    "message": record.message,
                    "received_timestamp_ms": record.received_timestamp_ms,
                }
            )
            for record in records
        )
        + "\n",
        encoding="utf-8",
    )


def _kalshi_record(
    *,
    timestamp_ms: int,
    yes_ask: Decimal,
    yes_bid: Decimal,
) -> PaperFeedRecord:
    return PaperFeedRecord(
        source="kalshi",
        received_timestamp_ms=timestamp_ms,
        message={
            "type": "ticker",
            "msg": {
                "market_ticker": MARKET_TICKER,
                "ts_ms": timestamp_ms,
                "yes_ask_dollars": str(yes_ask),
                "yes_ask_size_fp": "100.00",
                "yes_bid_dollars": str(yes_bid),
                "yes_bid_size_fp": "100.00",
            },
        },
    )


if __name__ == "__main__":
    unittest.main()
