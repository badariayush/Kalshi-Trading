from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from kalshi_crypto.cli import main


class DoctorCliTests(unittest.TestCase):
    def test_doctor_accepts_safe_paper_config_without_execution(self) -> None:
        stdout = StringIO()

        with redirect_stdout(stdout):
            exit_code = main(["doctor", "--config", "configs/paper.example.toml"])

        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("mode=paper_simulated", output)
        self.assertIn("trade_mcp=disabled", output)
        self.assertIn("execution=not_attempted", output)

    def test_doctor_rejects_live_config_without_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "live.toml"
            path.write_text(
                """
[runtime]
mode = "live"
""".strip(),
                encoding="utf-8",
            )
            stderr = StringIO()

            with redirect_stderr(stderr):
                exit_code = main(["doctor", "--config", str(path)])

        self.assertEqual(exit_code, 2)
        self.assertIn("live mode requires confirm_live", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
