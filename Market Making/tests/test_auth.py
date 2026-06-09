from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from mm_bot.auth import WS_SIGNING_PATH, websocket_headers


class AuthTests(unittest.TestCase):
    def test_websocket_headers_have_required_names(self) -> None:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kalshi.key"
            path.write_bytes(
                key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            )
            headers = websocket_headers("kid", path, now_ms="123")
        self.assertEqual(headers["KALSHI-ACCESS-KEY"], "kid")
        self.assertEqual(headers["KALSHI-ACCESS-TIMESTAMP"], "123")
        self.assertTrue(headers["KALSHI-ACCESS-SIGNATURE"])
        self.assertEqual(WS_SIGNING_PATH, "/trade-api/ws/v2")


if __name__ == "__main__":
    unittest.main()
