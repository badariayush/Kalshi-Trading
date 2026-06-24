from __future__ import annotations

from decimal import Decimal
import unittest

from kalshi_crypto.arbitrage import ArbitrageConfig, evaluate_true_arbitrage
from kalshi_crypto.models import BookQuote, Position, Side


def zero_fee(quantity: int, price: Decimal) -> Decimal:
    return Decimal("0")


class TrueArbitrageTests(unittest.TestCase):
    def test_authorizes_only_when_locked_cost_meets_margin(self) -> None:
        position = Position(
            market_ticker="KXBTCD-TEST",
            side=Side.YES,
            quantity=100,
            entry_price=Decimal("0.60"),
            entry_fee=Decimal("1.68"),
        )
        quote = BookQuote(price=Decimal("0.34"), depth=100, age_ms=100)
        config = ArbitrageConfig(
            min_arb_margin=Decimal("0.0200"),
            slippage_buffer=Decimal("0.0050"),
            max_book_age_ms=750,
        )

        decision = evaluate_true_arbitrage(position, quote, config)

        self.assertTrue(decision.authorized)
        self.assertEqual(decision.reason, "ok")
        self.assertEqual(decision.quantity, 100)
        self.assertEqual(decision.locked_in_cost, Decimal("0.9776"))

    def test_rejects_when_margin_not_met(self) -> None:
        position = Position(
            market_ticker="KXBTCD-TEST",
            side=Side.YES,
            quantity=100,
            entry_price=Decimal("0.60"),
            entry_fee=Decimal("1.68"),
        )
        quote = BookQuote(price=Decimal("0.37"), depth=100, age_ms=100)
        config = ArbitrageConfig(
            min_arb_margin=Decimal("0.0200"),
            slippage_buffer=Decimal("0.0050"),
            max_book_age_ms=750,
        )

        decision = evaluate_true_arbitrage(position, quote, config)

        self.assertFalse(decision.authorized)
        self.assertEqual(decision.reason, "margin_not_met")
        self.assertEqual(decision.locked_in_cost, Decimal("1.0082"))

    def test_exact_boundary_is_authorized(self) -> None:
        position = Position(
            market_ticker="KXBTCD-TEST",
            side=Side.YES,
            quantity=1,
            entry_price=Decimal("0.60"),
            entry_fee=Decimal("0"),
        )
        quote = BookQuote(price=Decimal("0.38"), depth=1, age_ms=100)
        config = ArbitrageConfig(
            min_arb_margin=Decimal("0.0200"),
            slippage_buffer=Decimal("0"),
            max_book_age_ms=750,
        )

        decision = evaluate_true_arbitrage(position, quote, config, fee_model=zero_fee)

        self.assertTrue(decision.authorized)
        self.assertEqual(decision.locked_in_cost, Decimal("0.98"))

    def test_rejects_stale_or_shallow_books_before_margin_math(self) -> None:
        position = Position(
            market_ticker="KXBTCD-TEST",
            side=Side.YES,
            quantity=10,
            entry_price=Decimal("0.60"),
            entry_fee=Decimal("0"),
        )
        config = ArbitrageConfig(
            min_arb_margin=Decimal("0.0200"),
            slippage_buffer=Decimal("0"),
            max_book_age_ms=750,
        )

        stale = evaluate_true_arbitrage(
            position,
            BookQuote(price=Decimal("0.10"), depth=10, age_ms=751),
            config,
            fee_model=zero_fee,
        )
        shallow = evaluate_true_arbitrage(
            position,
            BookQuote(price=Decimal("0.10"), depth=9, age_ms=100),
            config,
            fee_model=zero_fee,
        )

        self.assertFalse(stale.authorized)
        self.assertEqual(stale.reason, "stale_book")
        self.assertFalse(shallow.authorized)
        self.assertEqual(shallow.reason, "insufficient_depth")


if __name__ == "__main__":
    unittest.main()
