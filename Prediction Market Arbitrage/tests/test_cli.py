from __future__ import annotations

import io
from contextlib import redirect_stdout
import unittest

from arb_bot.cli import main


class CliTests(unittest.TestCase):
    def test_demo_command_prints_opportunity(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["demo"])
        self.assertEqual(code, 0)
        self.assertIn("polymarket_yes__kalshi_no", out.getvalue())


if __name__ == "__main__":
    unittest.main()
