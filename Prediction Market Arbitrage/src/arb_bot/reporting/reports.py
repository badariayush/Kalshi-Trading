from __future__ import annotations

from pathlib import Path
import sqlite3


def summarize_database(path: str | Path) -> str:
    db_path = Path(path)
    if not db_path.exists():
        return f"No database found at {db_path}"
    conn = sqlite3.connect(db_path)
    try:
        opps = conn.execute("SELECT COUNT(*), COALESCE(SUM(CAST(expected_profit AS REAL)), 0) FROM opportunities").fetchone()
        fills = conn.execute("SELECT COUNT(*), COALESCE(SUM(CAST(expected_profit AS REAL)), 0) FROM paper_fills").fetchone()
        positions = conn.execute(
            """
            SELECT
                COUNT(*),
                COALESCE(SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN status = 'resolved' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CAST(expected_profit AS REAL)), 0),
                COALESCE(SUM(CAST(realized_profit AS REAL)), 0)
            FROM paper_positions
            """
        ).fetchone()
        markets = conn.execute("SELECT venue, COUNT(*) FROM venue_markets GROUP BY venue ORDER BY venue").fetchall()
        pairs = conn.execute("SELECT COUNT(*) FROM market_pairs").fetchone()
        events = conn.execute("SELECT event_type, severity, COUNT(*) FROM runtime_events GROUP BY event_type, severity ORDER BY event_type, severity").fetchall()
        candidates = _load_pair_candidates(conn)
    finally:
        conn.close()
    lines = [
        f"Opportunities: {opps[0]} expected_profit={opps[1]:.4f}",
        f"Paper fills: {fills[0]} expected_profit={fills[1]:.4f}",
        f"Paper positions: {positions[0]} open={positions[1]} resolved={positions[2]} expected_profit={positions[3]:.4f} realized_profit={positions[4]:.4f}",
        f"Market pairs: {pairs[0]}",
        "Markets discovered:",
    ]
    if markets:
        for venue, count in markets:
            lines.append(f"  {venue}: {count}")
    else:
        lines.append("  none")
    lines.append("Runtime events:")
    if events:
        for event_type, severity, count in events:
            lines.append(f"  {event_type}/{severity}: {count}")
    else:
        lines.append("  none")
    lines.append("Top pair candidates:")
    if candidates:
        for confidence, passed, poly_title, kalshi_title in candidates:
            marker = "PASS" if passed else "below-threshold"
            lines.append(f"  {float(confidence):.4f} {marker}")
            lines.append(f"    Polymarket: {_trim(poly_title)}")
            lines.append(f"    Kalshi: {_trim(kalshi_title)}")
    else:
        lines.append("  none recorded")
    return "\n".join(lines)


def _load_pair_candidates(conn: sqlite3.Connection) -> list[tuple[str, int, str, str]]:
    table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'pair_candidates'"
    ).fetchone()
    if table is None:
        return []
    return conn.execute(
        """
        SELECT confidence, passed_threshold, polymarket_title, kalshi_title
        FROM pair_candidates
        ORDER BY CAST(confidence AS REAL) DESC
        LIMIT 10
        """
    ).fetchall()


def _trim(value: str, limit: int = 120) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."
