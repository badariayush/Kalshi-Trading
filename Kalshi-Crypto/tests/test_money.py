from __future__ import annotations

from decimal import Decimal
import unittest

from kalshi_crypto.money import (
    kalshi_general_maker_fee,
    kalshi_general_taker_fee,
    round_up_to_cent,
)


class MoneyMathTests(unittest.TestCase):
    def test_round_up_to_cent(self) -> None:
        self.assertEqual(round_up_to_cent(Decimal("1.73124")), Decimal("1.74"))
        self.assertEqual(round_up_to_cent(Decimal("1.7300")), Decimal("1.73"))
        self.assertEqual(round_up_to_cent(Decimal("0.001")), Decimal("0.01"))

    def test_kalshi_general_taker_fee_uses_contract_count_price_and_round_up(self) -> None:
        self.assertEqual(kalshi_general_taker_fee(100, Decimal("0.46")), Decimal("1.74"))
        self.assertEqual(kalshi_general_taker_fee(100, Decimal("0.51")), Decimal("1.75"))
        self.assertEqual(kalshi_general_taker_fee(1, Decimal("0.50")), Decimal("0.02"))

    def test_kalshi_general_maker_fee_uses_lower_rate(self) -> None:
        self.assertEqual(kalshi_general_maker_fee(100, Decimal("0.46")), Decimal("0.44"))

    def test_fee_rejects_invalid_price_or_quantity(self) -> None:
        with self.assertRaises(ValueError):
            kalshi_general_taker_fee(0, Decimal("0.50"))
        with self.assertRaises(ValueError):
            kalshi_general_taker_fee(1, Decimal("1.00"))


if __name__ == "__main__":
    unittest.main()
