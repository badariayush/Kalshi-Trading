from __future__ import annotations

import unittest

from arb_bot.matching.pair_scorer import score_title_pair


class MatchingTests(unittest.TestCase):
    def test_scores_eligible_category(self) -> None:
        candidate = score_title_pair("poly", "kalshi", "Will BTC be above 100k?", "Will BTC be above 100k?", "crypto")
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertGreaterEqual(candidate.confidence, 0.9)

    def test_rejects_ineligible_category(self) -> None:
        candidate = score_title_pair("poly", "kalshi", "Election outcome", "Election outcome", "politics")
        self.assertIsNone(candidate)


if __name__ == "__main__":
    unittest.main()
