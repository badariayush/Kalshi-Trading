from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from kalshi_crypto.audit import redacted_event_dict
from kalshi_crypto.events import AuditEvent


class SQLiteAuditStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def append(self, event: AuditEvent) -> None:
        record = redacted_event_dict(event)
        with closing(sqlite3.connect(self.path)) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO audit_events (
                        event_id,
                        event_type,
                        worker,
                        timestamp_ms,
                        causality_id,
                        record_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["event_id"],
                        record["event_type"],
                        record["worker"],
                        record["timestamp_ms"],
                        record["causality_id"],
                        json.dumps(record, sort_keys=True, separators=(",", ":")),
                    ),
                )

    def read_all(self) -> list[dict[str, Any]]:
        with closing(sqlite3.connect(self.path)) as connection:
            rows = connection.execute(
                    """
                    SELECT record_json
                    FROM audit_events
                    ORDER BY sequence
                    """
                ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def read_report_events(self) -> list[dict[str, Any]]:
        with closing(sqlite3.connect(self.path)) as connection:
            rows = connection.execute(
                """
                SELECT record_json
                FROM audit_events
                WHERE event_type IN (
                    'LiveDataAuditCompleted',
                    'ExecutionFailed',
                    'SimulatedOrderPlaced',
                    'PositionClosed'
                )
                ORDER BY sequence
                """
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def latest_sequence(self) -> int:
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM audit_events"
            ).fetchone()
        return int(row[0])

    def has_event_type(self, event_type: str) -> bool:
        if not event_type:
            raise ValueError("event_type is required")
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute(
                "SELECT 1 FROM audit_events WHERE event_type = ? LIMIT 1",
                (event_type,),
            ).fetchone()
        return row is not None

    def _initialize(self) -> None:
        with closing(sqlite3.connect(self.path)) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS audit_events (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_id TEXT NOT NULL UNIQUE,
                        event_type TEXT NOT NULL,
                        worker TEXT NOT NULL,
                        timestamp_ms INTEGER NOT NULL,
                        causality_id TEXT NOT NULL,
                        record_json TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_audit_report_events
                    ON audit_events (event_type, sequence)
                    WHERE event_type IN (
                        'LiveDataAuditCompleted',
                        'ExecutionFailed',
                        'SimulatedOrderPlaced',
                        'PositionClosed'
                    )
                    """
                )
