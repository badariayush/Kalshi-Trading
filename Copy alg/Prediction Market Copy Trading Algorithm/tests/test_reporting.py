from __future__ import annotations

import unittest

from kalshi_algo.reporting import decode_market_ticker


class ReportingTests(unittest.TestCase):
    def test_decodes_crypto_threshold_and_15m_markets(self) -> None:
        self.assertEqual(
            decode_market_ticker("KXBTCD-26MAY2800-T73499.99"),
            "Bitcoin daily threshold: 73,499.99",
        )
        self.assertEqual(
            decode_market_ticker("KXHYPE15M-26MAY272330-30"),
            "HYPE crypto 15-min market, 23:30 window",
        )

    def test_decodes_sports_markets(self) -> None:
        self.assertEqual(
            decode_market_ticker("KXWNBASPREAD-26MAY27CONNPDX-PDX8"),
            "WNBA spread: Connecticut vs Portland, Portland 8",
        )
        self.assertEqual(
            decode_market_ticker("KXWNBATOTAL-26MAY27WSHSEA-165"),
            "WNBA total: Washington vs Seattle, 165",
        )

    def test_decodes_tennis_and_commodity_markets(self) -> None:
        self.assertEqual(
            decode_market_ticker("KXITFWMATCH-26MAY27MATLIN-LIN"),
            "ITF women tennis: Mat vs Lin, Lin side",
        )
        self.assertEqual(
            decode_market_ticker("KXWTI-26MAY2814-T88.99"),
            "WTI crude oil threshold: 88.99",
        )


if __name__ == "__main__":
    unittest.main()
