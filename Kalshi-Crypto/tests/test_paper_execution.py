from __future__ import annotations

from decimal import Decimal
from io import StringIO
import unittest

from kalshi_crypto.models import Position, Side
from kalshi_crypto.paper_execution import (
    PaperExecutionOrder,
    execute_paper_entry,
    execute_paper_exit,
)


class PaperExecutionTests(unittest.TestCase):
    def test_entry_prints_order_line_and_emits_audit_events(self) -> None:
        stdout = StringIO()
        order = PaperExecutionOrder(
            market_ticker="KXBTCD-TEST",
            side=Side.YES,
            quantity=10,
            price=Decimal("0.47"),
            authorization_event_id="auth-1",
            timestamp_ms=1_000,
        )

        result = execute_paper_entry(order, stdout=stdout)

        self.assertIn(
            "order placed for KXBTCD-TEST YES at 0.47 for 10 contracts (paper)",
            stdout.getvalue(),
        )
        self.assertIn(
            "order filled for KXBTCD-TEST YES at 0.47 for 10 contracts (paper)",
            stdout.getvalue(),
        )
        self.assertEqual(result.position.market_ticker, "KXBTCD-TEST")
        self.assertEqual(result.position.side, Side.YES)
        self.assertEqual(result.position.quantity, 10)
        self.assertEqual(
            [event.event_type for event in result.events],
            ["OrderSubmitted", "FillRecorded"],
        )
        self.assertEqual(result.events[0].payload["execution_backend"], "paper_simulated")
        self.assertEqual(result.events[1].payload["fee"], "0.18")

    def test_exit_prints_profit_line_and_emits_closed_position_event(self) -> None:
        stdout = StringIO()
        position = Position(
            market_ticker="KXBTCD-TEST",
            side=Side.YES,
            quantity=10,
            entry_price=Decimal("0.47"),
            entry_fee=Decimal("0.18"),
        )

        result = execute_paper_exit(
            position=position,
            exit_price=Decimal("0.64"),
            authorization_event_id="exit-auth-1",
            timestamp_ms=2_000,
            stdout=stdout,
        )

        self.assertIn(
            "order sold for KXBTCD-TEST YES at 0.64 for 10 contracts",
            stdout.getvalue(),
        )
        self.assertIn("profit 1.35 (paper)", stdout.getvalue())
        self.assertEqual(
            [event.event_type for event in result.events],
            ["OrderSubmitted", "FillRecorded", "PositionClosed"],
        )
        self.assertEqual(result.realized_pnl, Decimal("1.35"))
        self.assertEqual(result.events[-1].payload["outcome"], "profit")
        self.assertEqual(result.events[-1].payload["realized_pnl"], "1.35")

    def test_exit_prints_loss_line_when_pnl_is_negative(self) -> None:
        stdout = StringIO()
        position = Position(
            market_ticker="KXBTCD-TEST",
            side=Side.NO,
            quantity=5,
            entry_price=Decimal("0.58"),
            entry_fee=Decimal("0.09"),
        )

        result = execute_paper_exit(
            position=position,
            exit_price=Decimal("0.31"),
            authorization_event_id="exit-auth-2",
            timestamp_ms=2_000,
            stdout=stdout,
        )

        self.assertIn("loss 1.52 (paper)", stdout.getvalue())
        self.assertEqual(result.realized_pnl, Decimal("-1.52"))
        self.assertEqual(result.events[-1].payload["outcome"], "loss")

    def test_rejects_invalid_paper_order(self) -> None:
        with self.assertRaisesRegex(ValueError, "quantity"):
            PaperExecutionOrder(
                market_ticker="KXBTCD-TEST",
                side=Side.YES,
                quantity=0,
                price=Decimal("0.47"),
                authorization_event_id="auth-1",
                timestamp_ms=1_000,
            )


if __name__ == "__main__":
    unittest.main()
