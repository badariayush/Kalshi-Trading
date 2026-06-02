from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any
import json
import re

from .persistence import EventStore


@dataclass(slots=True)
class TradeRecap:
    position_id: str
    market_ticker: str
    market_name: str
    side: str
    signal_strength: str
    entry_time: str
    exit_time: str | None
    entry_price: float
    exit_price: float | None
    size: int
    pnl: float
    entry_reason: str
    exit_reason: str | None
    stop_price: float | None
    take_profit_price: float | None
    orderbook: dict[str, Any]

    def render(self, index: int) -> list[str]:
        status = "closed" if self.exit_time else "open"
        exit_price = f"{self.exit_price:.4f}" if self.exit_price is not None else "-"
        lines = [
            f"{index}. {self.market_name}",
            f"   ticker={self.market_ticker} side={self.side} {status} signal={self.signal_strength} pnl={self.pnl:.4f}",
            f"   entry={self.entry_price:.4f} exit={exit_price} size={self.size} entry_reason={self.entry_reason} exit_reason={self.exit_reason or '-'}",
            f"   entry_time={self.entry_time} exit_time={self.exit_time or '-'}",
        ]
        if self.stop_price is not None or self.take_profit_price is not None:
            stop = _format_optional_float(self.stop_price)
            take_profit = _format_optional_float(self.take_profit_price)
            lines.append(f"   stop={stop} take_profit={take_profit}")
        if self.orderbook:
            bid = _format_optional_float(self.orderbook.get("best_bid"))
            ask = _format_optional_float(self.orderbook.get("best_ask"))
            spread = _format_optional_float(self.orderbook.get("spread"))
            expected_exit = _format_optional_float(self.orderbook.get("expected_exit_price"))
            age = self.orderbook.get("age_seconds")
            age_text = f"{float(age):.2f}s" if age is not None else "-"
            lines.append(
                f"   orderbook_at_entry best_bid={bid} best_ask={ask} spread={spread} expected_exit={expected_exit} age={age_text}"
            )
        else:
            lines.append("   orderbook_at_entry not captured for this run")
        return lines


@dataclass(slots=True)
class SessionReport:
    total_entries: int
    total_exits: int
    total_halts: int
    total_pnl: float
    pnl_by_signal: dict[str, float]
    rejected_reasons: dict[str, int]
    trades: list[TradeRecap]

    def render(self) -> str:
        lines = [
            f"Entries: {self.total_entries}",
            f"Exits: {self.total_exits}",
            f"Halts: {self.total_halts}",
            f"Realized PnL: {self.total_pnl:.4f}",
            "PnL by signal:",
        ]
        for key, value in sorted(self.pnl_by_signal.items()):
            lines.append(f"  {key}: {value:.4f}")
        lines.append("Rejected signals:")
        for key, value in sorted(self.rejected_reasons.items()):
            lines.append(f"  {key}: {value}")
        lines.append("")
        lines.append("Markets entered:")
        entered = sorted({(trade.market_name, trade.market_ticker) for trade in self.trades})
        if entered:
            for market_name, market_ticker in entered:
                lines.append(f"  {market_name} ({market_ticker})")
        else:
            lines.append("  No entries recorded.")
        lines.append("")
        lines.append("Trade recaps:")
        if not self.trades:
            lines.append("  No entries recorded.")
        for index, trade in enumerate(self.trades, start=1):
            lines.extend(trade.render(index))
        return "\n".join(lines)


def generate_session_report(store: EventStore) -> SessionReport:
    rows = store.load_events()
    pnl_by_signal: dict[str, float] = defaultdict(float)
    rejected = Counter()
    entries = 0
    exits = 0
    halts = 0
    total_pnl = 0.0
    open_trades: dict[str, dict[str, Any]] = {}
    trade_recaps: list[TradeRecap] = []

    for row in rows:
        event_type = row["event_type"]
        data = json.loads(row["payload_json"])
        metadata = data.get("metadata", {})
        if event_type == "ENTER":
            entries += 1
            position_id = metadata.get("position_id")
            if position_id:
                open_trades[position_id] = {
                    "position_id": position_id,
                    "market_ticker": data.get("market_ticker") or row["market_ticker"] or "-",
                    "side": data.get("side") or row["side"] or "-",
                    "signal_strength": data.get("signal_strength") or row["signal_strength"] or "unknown",
                    "entry_time": data.get("timestamp") or row["timestamp"],
                    "entry_price": float(data.get("price") or row["price"] or 0.0),
                    "size": int(data.get("size") or row["size"] or 0),
                    "entry_reason": data.get("reason") or row["reason"],
                    "stop_price": _optional_float(metadata.get("stop_price")),
                    "take_profit_price": _optional_float(metadata.get("take_profit_price")),
                    "orderbook": metadata.get("orderbook") or {},
                }
        elif event_type == "EXIT":
            exits += 1
            pnl = float(metadata.get("pnl", 0.0))
            total_pnl += pnl
            strength = data.get("signal_strength") or "unknown"
            pnl_by_signal[strength] += pnl
            position_id = metadata.get("position_id")
            entry = open_trades.pop(position_id, None) if position_id else None
            if entry is None:
                entry = {
                    "position_id": position_id or "-",
                    "market_ticker": data.get("market_ticker") or row["market_ticker"] or "-",
                    "side": data.get("side") or row["side"] or "-",
                    "signal_strength": data.get("signal_strength") or row["signal_strength"] or "unknown",
                    "entry_time": "-",
                    "entry_price": float(metadata.get("entry_price") or 0.0),
                    "size": int(data.get("size") or row["size"] or 0),
                    "entry_reason": "-",
                    "stop_price": None,
                    "take_profit_price": None,
                    "orderbook": {},
                }
            trade_recaps.append(
                TradeRecap(
                    position_id=str(entry["position_id"]),
                    market_ticker=str(entry["market_ticker"]),
                    market_name=decode_market_ticker(str(entry["market_ticker"])),
                    side=str(entry["side"]),
                    signal_strength=str(entry["signal_strength"]),
                    entry_time=str(entry["entry_time"]),
                    exit_time=data.get("timestamp") or row["timestamp"],
                    entry_price=float(entry["entry_price"]),
                    exit_price=float(data.get("price") or row["price"] or 0.0),
                    size=int(entry["size"]),
                    pnl=pnl,
                    entry_reason=str(entry["entry_reason"]),
                    exit_reason=data.get("reason") or row["reason"],
                    stop_price=entry["stop_price"],
                    take_profit_price=entry["take_profit_price"],
                    orderbook=dict(entry["orderbook"]),
                )
            )
        elif event_type == "HALT":
            halts += 1
        elif event_type == "REJECT":
            rejected[row["reason"]] += 1

    for entry in open_trades.values():
        trade_recaps.append(
            TradeRecap(
                position_id=str(entry["position_id"]),
                market_ticker=str(entry["market_ticker"]),
                market_name=decode_market_ticker(str(entry["market_ticker"])),
                side=str(entry["side"]),
                signal_strength=str(entry["signal_strength"]),
                entry_time=str(entry["entry_time"]),
                exit_time=None,
                entry_price=float(entry["entry_price"]),
                exit_price=None,
                size=int(entry["size"]),
                pnl=0.0,
                entry_reason=str(entry["entry_reason"]),
                exit_reason=None,
                stop_price=entry["stop_price"],
                take_profit_price=entry["take_profit_price"],
                orderbook=dict(entry["orderbook"]),
            )
        )

    return SessionReport(
        total_entries=entries,
        total_exits=exits,
        total_halts=halts,
        total_pnl=total_pnl,
        pnl_by_signal=dict(pnl_by_signal),
        rejected_reasons=dict(rejected),
        trades=trade_recaps,
    )


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _format_optional_float(value: Any) -> str:
    if value is None:
        return "-"
    return f"{float(value):.4f}"


def decode_market_ticker(ticker: str) -> str:
    match = re.match(r"^KX([A-Z0-9]+)-(.+)$", ticker)
    if not match:
        return ticker

    product, suffix = match.groups()
    parsed = _split_date_and_contract(suffix)
    if parsed is None:
        return ticker
    date_code, contract = parsed
    date_text = _decode_date_code(date_code)

    if product.endswith("15M"):
        asset = product[:-3]
        window = _decode_time_from_date_code(date_code)
        return f"{asset} crypto 15-min market, {window} window"

    if product in {"BTCD", "ETHD"} and contract.startswith("T"):
        asset = {"BTCD": "Bitcoin", "ETHD": "Ethereum"}[product]
        return f"{asset} daily threshold: {_format_threshold(contract[1:])}"

    if product in {"WTI", "BRENTD"} and contract.startswith("T"):
        asset = {"WTI": "WTI crude oil", "BRENTD": "Brent crude oil"}[product]
        return f"{asset} threshold: {_format_threshold(contract[1:])}"

    if product.startswith("INX") and contract.startswith("T"):
        return f"Index market threshold: {_format_threshold(contract[1:])}"

    sports = _decode_sports_market(product, contract)
    if sports is not None:
        return sports

    tennis = _decode_tennis_market(product, contract)
    if tennis is not None:
        return tennis

    return f"{product} {date_text}: {contract}"


def _decode_sports_market(product: str, contract: str) -> str | None:
    sport_prefixes = {
        "WNBASPREAD": ("WNBA", "spread"),
        "WNBATOTAL": ("WNBA", "total"),
        "WNBAGAME": ("WNBA", "game winner"),
        "NBASPREAD": ("NBA", "spread"),
        "NBATOTAL": ("NBA", "total"),
        "NBAGAME": ("NBA", "game winner"),
        "MLBF5SPREAD": ("MLB first-5", "spread"),
        "MLBTOTAL": ("MLB", "total"),
        "MLBGAME": ("MLB", "game winner"),
    }
    decoded = sport_prefixes.get(product)
    if decoded is None:
        return None

    league, market_type = decoded
    parts = contract.split("-")
    if len(parts) < 2:
        return None
    matchup = _decode_matchup_code(parts[0])
    value = parts[1]
    if market_type == "spread":
        team, spread = _split_team_number(value)
        spread_text = f"{_team_name(team)} {spread}" if spread else _team_name(value)
        return f"{league} spread: {matchup}, {spread_text}"
    if market_type == "total":
        return f"{league} total: {matchup}, {value}"
    return f"{league} game winner: {matchup}, {_team_name(value)} side"


def _decode_tennis_market(product: str, contract: str) -> str | None:
    labels = {
        "ITFWMATCH": "ITF women tennis",
        "ITFMATCH": "ITF tennis",
        "ATPMATCH": "ATP tennis",
        "WTAMATCH": "WTA tennis",
    }
    label = labels.get(product)
    if label is None:
        return None

    parts = contract.split("-")
    if len(parts) < 2:
        return None
    matchup_code, side_code = parts[0], parts[1]
    player_a, player_b = _split_tennis_matchup(matchup_code, side_code)
    side = _format_code_name(side_code)
    return f"{label}: {player_a} vs {player_b}, {side} side"


def _decode_matchup_code(value: str) -> str:
    known = sorted(_team_codes(), key=len, reverse=True)
    for left in known:
        if not value.startswith(left):
            continue
        right = value[len(left) :]
        if right in known:
            return f"{_team_name(left)} vs {_team_name(right)}"
    if len(value) >= 6:
        left = value[: len(value) // 2]
        right = value[len(value) // 2 :]
        return f"{_team_name(left)} vs {_team_name(right)}"
    return _format_code_name(value)


def _split_tennis_matchup(matchup_code: str, side_code: str) -> tuple[str, str]:
    if matchup_code.endswith(side_code) and len(matchup_code) > len(side_code):
        return _format_code_name(matchup_code[: -len(side_code)]), _format_code_name(side_code)
    if matchup_code.startswith(side_code) and len(matchup_code) > len(side_code):
        return _format_code_name(side_code), _format_code_name(matchup_code[len(side_code) :])
    midpoint = len(matchup_code) // 2
    return _format_code_name(matchup_code[:midpoint]), _format_code_name(matchup_code[midpoint:])


def _split_team_number(value: str) -> tuple[str, str]:
    match = re.match(r"^([A-Z]+)(\d+(?:\.\d+)?)$", value)
    if match is None:
        return value, ""
    team, number = match.groups()
    return team, number


def _team_name(code: str) -> str:
    return _team_codes().get(code, _format_code_name(code))


def _team_codes() -> dict[str, str]:
    return {
        "CONN": "Connecticut",
        "PDX": "Portland",
        "WSH": "Washington",
        "SEA": "Seattle",
        "OKC": "OKC",
        "SAS": "San Antonio",
        "COL": "Rockies",
        "LAD": "Dodgers",
    }


def _format_code_name(value: str) -> str:
    if not value:
        return value
    return value[0] + value[1:].lower()


def _decode_date_code(value: str) -> str:
    match = re.match(r"^(\d{2})([A-Z]{3})(\d{2})(?:H?(\d{2}|\d{4}))?$", value)
    if match is None:
        return value
    year, month, day, time_text = match.groups()
    text = f"20{year}-{month.title()}-{day}"
    if time_text:
        text += f" {_format_time(time_text)}"
    return text


def _decode_time_from_date_code(value: str) -> str:
    match = re.match(r"^\d{2}[A-Z]{3}\d{2}(?:H?(\d{2}|\d{4}))?$", value)
    if match is None or match.group(1) is None:
        return "unknown"
    return _format_time(match.group(1))


def _format_time(value: str) -> str:
    if len(value) == 2:
        return f"{value}:00"
    return f"{value[:2]}:{value[2:]}"


def _split_date_and_contract(value: str) -> tuple[str, str] | None:
    match = re.match(r"^(\d{2}[A-Z]{3}\d{2})(.*)$", value)
    if match is None:
        return None
    date_code, remainder = match.groups()
    time_match = re.match(r"^(H?\d{2,4})(.*)$", remainder)
    if time_match is not None:
        time_code, remainder = time_match.groups()
        date_code += time_code
    contract = remainder.removeprefix("-")
    if not contract:
        return None
    return date_code, contract


def _format_threshold(value: str) -> str:
    try:
        return f"{float(value):,.2f}".rstrip("0").rstrip(".")
    except ValueError:
        return value
