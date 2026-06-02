from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3

from .models import ActionEvent


class EventStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS action_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                market_ticker TEXT,
                side TEXT,
                price REAL,
                size INTEGER,
                signal_strength TEXT,
                confidence_level TEXT,
                reason TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def append(self, event: ActionEvent) -> None:
        self.conn.execute(
            """
            INSERT INTO action_events (
                event_type, timestamp, market_ticker, side, price, size,
                signal_strength, confidence_level, reason, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_type,
                event.timestamp.isoformat(),
                event.market_ticker,
                event.side,
                event.price,
                event.size,
                event.signal_strength,
                event.confidence_level,
                event.reason,
                event.to_json(),
            ),
        )
        self.conn.commit()

    def load_events(self) -> list[sqlite3.Row]:
        cursor = self.conn.execute("SELECT * FROM action_events ORDER BY timestamp ASC, id ASC")
        return list(cursor.fetchall())

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "EventStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
