from pathlib import Path

_SRC_PACKAGE = Path(__file__).resolve().parent.parent / "src" / "kalshi_algo"
__path__ = [str(_SRC_PACKAGE)]

with open(_SRC_PACKAGE / "__init__.py", "r", encoding="utf-8") as _handle:
    exec(_handle.read(), globals())
