CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    size TEXT NOT NULL,
    gross_cost TEXT NOT NULL,
    net_cost TEXT NOT NULL,
    net_edge TEXT NOT NULL,
    expected_profit TEXT NOT NULL,
    detected_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS venue_markets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    venue TEXT NOT NULL,
    market_id TEXT NOT NULL,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    yes_token_id TEXT,
    no_token_id TEXT,
    close_time TEXT,
    discovered_at REAL NOT NULL,
    UNIQUE(venue, market_id)
);

CREATE TABLE IF NOT EXISTS market_pairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    polymarket_market_id TEXT NOT NULL,
    kalshi_market_id TEXT NOT NULL,
    confidence TEXT NOT NULL,
    status TEXT NOT NULL,
    discovered_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS pair_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    polymarket_market_id TEXT NOT NULL,
    kalshi_market_id TEXT NOT NULL,
    polymarket_title TEXT NOT NULL,
    kalshi_title TEXT NOT NULL,
    polymarket_category TEXT NOT NULL,
    kalshi_category TEXT NOT NULL,
    confidence TEXT NOT NULL,
    passed_threshold INTEGER NOT NULL,
    discovered_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id INTEGER,
    pair_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    size TEXT NOT NULL,
    expected_profit TEXT NOT NULL,
    filled_at REAL NOT NULL,
    status TEXT NOT NULL,
    FOREIGN KEY(opportunity_id) REFERENCES opportunities(id)
);

CREATE TABLE IF NOT EXISTS paper_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id INTEGER,
    pair_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    status TEXT NOT NULL,
    size TEXT NOT NULL,
    yes_venue TEXT NOT NULL,
    yes_market_id TEXT NOT NULL,
    yes_entry_price TEXT NOT NULL,
    no_venue TEXT NOT NULL,
    no_market_id TEXT NOT NULL,
    no_entry_price TEXT NOT NULL,
    entry_cost TEXT NOT NULL,
    expected_profit TEXT NOT NULL,
    opened_at REAL NOT NULL,
    resolved_at REAL,
    winning_side TEXT,
    realized_payout TEXT,
    realized_profit TEXT,
    FOREIGN KEY(opportunity_id) REFERENCES opportunities(id)
);
