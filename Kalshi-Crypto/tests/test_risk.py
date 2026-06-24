from __future__ import annotations

from decimal import Decimal
import unittest

from kalshi_crypto.models import BookQuote, Position, Side
from kalshi_crypto.risk import (
    PartialHedgeConfig,
    solve_partial_hedge,
    wrong_side_loss,
)


def zero_fee(quantity: int, price: Decimal) -> Decimal:
    return Decimal("0")


class PartialHedgeTests(unittest.TestCase):
    def test_solves_smallest_quantity_that_caps_wrong_side_loss(self) -> None:
        position = Position(
            market_ticker="KXBTCD-TEST",
            side=Side.YES,
            quantity=100,
            entry_price=Decimal("0.60"),
            entry_fee=Decimal("0"),
        )
        quote = BookQuote(price=Decimal("0.55"), depth=100, age_ms=100)
        config = PartialHedgeConfig(
            opposing_ask_trigger=Decimal("0.50"),
            max_loss=Decimal("30.00"),
            max_contracts_pct_of_original_size=Decimal("1.00"),
            slippage_buffer=Decimal("0"),
            max_book_age_ms=750,
        )

        decision = solve_partial_hedge(position, quote, config, fee_model=zero_fee)

        self.assertTrue(decision.authorized)
        self.assertEqual(decision.reason, "ok")
        self.assertEqual(decision.quantity, 67)
        self.assertEqual(decision.wrong_side_loss, Decimal("29.85"))
        self.assertGreater(
            wrong_side_loss(position, 66, quote.price, Decimal("0"), zero_fee),
            config.max_loss,
        )

    def test_stays_inactive_until_trigger_crosses(self) -> None:
        position = Position(
            market_ticker="KXBTCD-TEST",
            side=Side.YES,
            quantity=100,
            entry_price=Decimal("0.60"),
            entry_fee=Decimal("0"),
        )
        config = PartialHedgeConfig(
            opposing_ask_trigger=Decimal("0.50"),
            max_loss=Decimal("30.00"),
            max_contracts_pct_of_original_size=Decimal("1.00"),
            slippage_buffer=Decimal("0"),
            max_book_age_ms=750,
        )

        decision = solve_partial_hedge(
            position,
            BookQuote(price=Decimal("0.49"), depth=100, age_ms=100),
            config,
            fee_model=zero_fee,
        )

        self.assertFalse(decision.authorized)
        self.assertEqual(decision.reason, "trigger_not_crossed")

    def test_rejects_when_depth_or_max_contract_cap_cannot_reach_loss_target(self) -> None:
        position = Position(
            market_ticker="KXBTCD-TEST",
            side=Side.YES,
            quantity=100,
            entry_price=Decimal("0.60"),
            entry_fee=Decimal("0"),
        )
        config = PartialHedgeConfig(
            opposing_ask_trigger=Decimal("0.50"),
            max_loss=Decimal("30.00"),
            max_contracts_pct_of_original_size=Decimal("1.00"),
            slippage_buffer=Decimal("0"),
            max_book_age_ms=750,
        )
        shallow = solve_partial_hedge(
            position,
            BookQuote(price=Decimal("0.55"), depth=50, age_ms=100),
            config,
            fee_model=zero_fee,
        )
        capped = solve_partial_hedge(
            position,
            BookQuote(price=Decimal("0.55"), depth=100, age_ms=100),
            PartialHedgeConfig(
                opposing_ask_trigger=Decimal("0.50"),
                max_loss=Decimal("30.00"),
                max_contracts_pct_of_original_size=Decimal("0.40"),
                slippage_buffer=Decimal("0"),
                max_book_age_ms=750,
            ),
            fee_model=zero_fee,
        )

        self.assertFalse(shallow.authorized)
        self.assertEqual(shallow.reason, "insufficient_depth_for_loss_cap")
        self.assertEqual(shallow.required_quantity, 67)
        self.assertFalse(capped.authorized)
        self.assertEqual(capped.reason, "max_loss_target_unreachable")


if __name__ == "__main__":
    unittest.main()
