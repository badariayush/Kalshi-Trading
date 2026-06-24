from __future__ import annotations

import unittest

from kalshi_crypto.config import ConfigError, RuntimeConfig, RuntimeMode
from kalshi_crypto.execution import (
    ExecutionBackend,
    ExecutionIntent,
    SafetyError,
    validate_execution_intent,
)


class ConfigAndExecutionSafetyTests(unittest.TestCase):
    def test_runtime_defaults_to_paper_simulated(self) -> None:
        config = RuntimeConfig.from_mapping({})

        self.assertEqual(config.mode, RuntimeMode.PAPER_SIMULATED)
        self.assertFalse(config.confirm_live)
        self.assertFalse(config.allow_trade_mcp)

    def test_live_mode_requires_explicit_confirmation(self) -> None:
        with self.assertRaisesRegex(ConfigError, "live mode requires confirm_live"):
            RuntimeConfig.from_mapping({"mode": "live"})

    def test_trade_execution_mcp_is_disabled_until_separate_review(self) -> None:
        with self.assertRaisesRegex(ConfigError, "trade execution MCP"):
            RuntimeConfig.from_mapping({"allow_trade_mcp": True})

        intent = ExecutionIntent(
            mode=RuntimeMode.PAPER_SIMULATED,
            backend=ExecutionBackend.TRADE_MCP,
            confirm_live=False,
        )
        with self.assertRaisesRegex(SafetyError, "pending security/risk review"):
            validate_execution_intent(intent)

    def test_live_direct_execution_requires_confirmation_even_without_mcp(self) -> None:
        intent = ExecutionIntent(
            mode=RuntimeMode.LIVE,
            backend=ExecutionBackend.DIRECT_KALSHI,
            confirm_live=False,
        )

        with self.assertRaisesRegex(SafetyError, "live execution requires confirm_live"):
            validate_execution_intent(intent)


if __name__ == "__main__":
    unittest.main()
