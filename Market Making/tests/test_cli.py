from __future__ import annotations

import io
from contextlib import redirect_stdout
import unittest
from unittest.mock import patch

from mm_bot.cli import main
from mm_bot.kalshi import Market


class CliTests(unittest.TestCase):
    def test_discover_prints_market_json(self) -> None:
        with patch("mm_bot.kalshi.KalshiRestClient.discover_btc_15m_markets", return_value=[Market("BTC", "Bitcoin test", None)]):
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["discover", "--config", "configs/config.example.toml"])
        self.assertEqual(code, 0)
        self.assertIn('"event": "MARKET_DISCOVERED"', out.getvalue())


if __name__ == "__main__":
    unittest.main()
