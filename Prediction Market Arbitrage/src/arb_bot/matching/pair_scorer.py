from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher
import re

from arb_bot.models import MarketPair, PairStatus, VenueMarket


@dataclass(frozen=True, slots=True)
class PairCandidate:
    polymarket_id: str
    kalshi_id: str
    polymarket_title: str
    kalshi_title: str
    category: str
    confidence: Decimal
    reason: str


ELIGIBLE_CATEGORIES = {"sports", "crypto", "weather"}


def score_title_pair(
    polymarket_id: str,
    kalshi_id: str,
    polymarket_title: str,
    kalshi_title: str,
    category: str,
) -> PairCandidate | None:
    normalized_category = category.lower().strip()
    if normalized_category not in ELIGIBLE_CATEGORIES:
        return None
    ratio = SequenceMatcher(None, polymarket_title.lower(), kalshi_title.lower()).ratio()
    confidence = Decimal(str(round(ratio, 4)))
    return PairCandidate(
        polymarket_id=polymarket_id,
        kalshi_id=kalshi_id,
        polymarket_title=polymarket_title,
        kalshi_title=kalshi_title,
        category=normalized_category,
        confidence=confidence,
        reason="title_similarity_v1",
    )


def auto_pair_markets(
    polymarket_markets: list[VenueMarket],
    kalshi_markets: list[VenueMarket],
    min_confidence: Decimal,
    max_pairs: int,
    require_same_category: bool = False,
) -> list[tuple[MarketPair, VenueMarket, VenueMarket]]:
    candidates: list[tuple[Decimal, MarketPair, VenueMarket, VenueMarket]] = []
    for poly in polymarket_markets:
        for kalshi in kalshi_markets:
            if require_same_category and kalshi.category != poly.category:
                continue
            confidence = _market_confidence(poly, kalshi)
            if confidence < min_confidence:
                continue
            category = poly.category if poly.category == kalshi.category else f"{poly.category}/{kalshi.category}"
            pair = MarketPair(
                pair_id=f"{poly.market_id}__{kalshi.market_id}",
                name=f"{poly.title} <> {kalshi.title}",
                category=category,
                polymarket_market_id=poly.market_id,
                kalshi_market_id=kalshi.market_id,
                confidence=confidence,
                status=PairStatus.ACTIVE,
            )
            candidates.append((confidence, pair, poly, kalshi))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [(pair, poly, kalshi) for _, pair, poly, kalshi in candidates[:max_pairs]]


def top_pair_candidates(
    polymarket_markets: list[VenueMarket],
    kalshi_markets: list[VenueMarket],
    limit: int = 25,
) -> list[tuple[Decimal, VenueMarket, VenueMarket]]:
    scored: list[tuple[Decimal, VenueMarket, VenueMarket]] = []
    for poly in polymarket_markets:
        poly_tokens = _token_set(_normalize_title(poly.title))
        if not poly_tokens:
            continue
        for kalshi in kalshi_markets:
            kalshi_tokens = _token_set(_normalize_title(kalshi.title))
            if not kalshi_tokens:
                continue
            if not poly_tokens.intersection(kalshi_tokens):
                continue
            scored.append((_market_confidence(poly, kalshi), poly, kalshi))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[:limit]


def _market_confidence(poly: VenueMarket, kalshi: VenueMarket) -> Decimal:
    poly_title = _normalize_title(poly.title)
    kalshi_title = _normalize_title(kalshi.title)
    title_ratio = Decimal(str(round(SequenceMatcher(None, poly_title, kalshi_title).ratio(), 4)))
    token_overlap = _token_overlap(poly_title, kalshi_title)
    confidence = (title_ratio * Decimal("0.70")) + (token_overlap * Decimal("0.30"))
    if _date_hint(poly.close_time) and _date_hint(poly.close_time) == _date_hint(kalshi.close_time):
        confidence += Decimal("0.05")
    return min(Decimal("1.0"), confidence.quantize(Decimal("0.0001")))


def _normalize_title(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", value.lower())).strip()


def _token_overlap(left: str, right: str) -> Decimal:
    left_tokens = _token_set(left)
    right_tokens = _token_set(right)
    if not left_tokens or not right_tokens:
        return Decimal("0")
    return Decimal(len(left_tokens & right_tokens)) / Decimal(len(left_tokens | right_tokens))


def _token_set(value: str) -> set[str]:
    stopwords = {
        "will",
        "the",
        "and",
        "yes",
        "not",
        "over",
        "under",
        "win",
        "wins",
        "price",
        "market",
        "next",
        "scored",
        "points",
    }
    return {token for token in value.split() if len(token) > 2 and token not in stopwords}


def _date_hint(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"\d{4}-\d{2}-\d{2}", value)
    return match.group(0) if match else None
