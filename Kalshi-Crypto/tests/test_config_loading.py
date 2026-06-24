from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from kalshi_crypto.config import (
    ConfigError,
    RuntimeMode,
    load_app_config,
)


class AppConfigLoadingTests(unittest.TestCase):
    def test_loads_safe_live_example_config(self) -> None:
        config = load_app_config("configs/live.example.toml")

        self.assertEqual(config.runtime.mode, RuntimeMode.LIVE_DATA)
        self.assertFalse(config.runtime.confirm_live)
        self.assertFalse(config.runtime.allow_trade_mcp)
        self.assertEqual(config.trade_management.min_arb_margin, Decimal("0.0200"))
        self.assertEqual(config.circuit_breakers.data_feed_stale_ms, 2_500)
        self.assertEqual(config.trade_management.partial_hedge_opposing_ask_trigger, Decimal("0.5000"))
        self.assertEqual(
            config.trade_management.partial_hedge_max_loss_for_original_risk(
                Decimal("60.00")
            ),
            Decimal("30.0000"),
        )

    def test_rejects_missing_config_file(self) -> None:
        with self.assertRaisesRegex(ConfigError, "config file not found"):
            load_app_config("configs/does-not-exist.toml")

    def test_rejects_trade_mcp_from_toml(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "unsafe.toml"
            path.write_text(
                """
[runtime]
mode = "live_data"
allow_trade_mcp = true
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ConfigError, "trade execution MCP"):
                load_app_config(path)

    def test_uses_more_conservative_partial_hedge_max_loss_when_both_limits_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "risk.toml"
            path.write_text(
                """
[runtime]
mode = "live_data"

[trade_management]
partial_hedge_max_loss_pct_of_original_risk = "0.75"
partial_hedge_max_loss_usd = "20.00"
""".strip(),
                encoding="utf-8",
            )

            config = load_app_config(path)

        self.assertEqual(
            config.trade_management.partial_hedge_max_loss_for_original_risk(
                Decimal("60.00")
            ),
            Decimal("20.00"),
        )


if __name__ == "__main__":
    unittest.main()
