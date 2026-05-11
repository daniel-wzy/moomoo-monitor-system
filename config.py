"""
Moomoo Alert System — Configuration
"""

# OpenD connection
OPEND_HOST = "127.0.0.1"
OPEND_PORT = 11111

# Timeframes to monitor (applies to ALL indicators)
TIMEFRAMES = ["30m", "1h", "4h", "daily"]

# Moomoo KLType mapping
KTYPE_MAP = {
    "30m": "K_30M",
    "1h": "K_60M",
    "4h": "K_60M",   # aggregate 4x 60min candles
    "daily": "K_DAY",
}

# History bars to fetch
HISTORY_BARS = {
    "30m": 500,
    "1h": 500,
    "4h": 800,
    "daily": 300,
}

# ─── Watchlist ───────────────────────────────────────────────────

# Window period stocks (窗口期 — tend to rise until cutoff date)
WINDOW_STOCKS = {
    "US.TSM":  {"earnings": "2026-04-16", "window_end": "2026-02-16"},
    "US.NET":  {"earnings": "2026-02-10", "window_end": "2026-02-15"},
    "US.DDOG": {"earnings": "2026-02-10", "window_end": "2026-02-11"},
    "US.SHOP": {"earnings": "2026-02-10", "window_end": "2026-02-04"},
    "US.SNAP": {"earnings": "2026-02-04", "window_end": "2026-02-04"},
    "US.ROKU": {"earnings": "2026-02-12", "window_end": "2026-02-15"},
    "US.PATH": {"earnings": "2026-03-10", "window_end": "2026-02-15"},
    "US.HIMS": {"earnings": "2026-02-23", "window_end": "2026-02-16"},
    "US.DUOL": {"earnings": "2026-02-26", "window_end": "2026-02-09"},
    "US.CIFR": {"earnings": "2026-02-24", "window_end": "2026-02-16"},
    "US.OPEN": {"earnings": "2026-02-26", "window_end": "2026-02-15"},
    "US.MP":   {"earnings": "2026-02-19", "window_end": "2026-02-16"},
    # "US.LSK" removed — ticker not found in Moomoo. Needs correct symbol.
    "US.BBAI": {"earnings": "2026-03-05", "window_end": "2026-03-06"},
    "US.OTEX": {"earnings": "2026-02-05", "window_end": "2026-01-28"},
}

# Big tech / popular stocks - expanded watchlist
BIG_TECH = [
    # Mega Cap Tech
    "US.NVDA",   # NVIDIA
    "US.AAPL",   # Apple
    "US.MSFT",   # Microsoft
    "US.GOOG",   # Alphabet
    "US.GOOGL",  # Alphabet (voting)
    "US.AMZN",   # Amazon
    "US.META",   # Meta
    "US.TSLA",   # Tesla
    # Semiconductors
    "US.AMD",    # AMD
    "US.AVGO",   # Broadcom
    "US.TSM",    # Taiwan Semi
    "US.ARM",    # ARM Holdings
    "US.MU",     # Micron
    "US.QCOM",   # Qualcomm
    "US.INTC",   # Intel
    "US.MRVL",   # Marvell
    # AI / Cloud / Software
    "US.PLTR",   # Palantir
    "US.CRWD",   # CrowdStrike
    "US.ANET",   # Arista Networks
    "US.SNOW",   # Snowflake
    "US.MDB",    # MongoDB
    # Fintech / Crypto
    "US.COIN",   # Coinbase
    "US.SOFI",   # SoFi
    "US.MSTR",   # MicroStrategy
    "US.SQ",     # Block (Square)
    "US.PYPL",   # PayPal
    "US.HOOD",   # Robinhood
    # Energy / Nuclear / AI Power
    "US.OKLO",   # Oklo (nuclear)
    "US.CEG",    # Constellation Energy
    "US.BW",     # Babcock & Wilcox
    "US.EOSE",   # Eos Energy
    "US.VST",    # Vistra
    # Photonics / Optical
    "US.COHR",   # Coherent
    "US.AAOI",   # Applied Optoelectronics
    # Pharma / Healthcare
    "US.LLY",    # Eli Lilly
    "US.NVO",    # Novo Nordisk
    # Consumer / Media
    "US.NFLX",   # Netflix
    "US.DIS",    # Disney
    "US.UBER",   # Uber
    "US.ABNB",   # Airbnb
    # Space / High Beta
    "US.RKLB",   # Rocket Lab
    # Materials
    "US.ALOY",   # Alloys
]

# Combined active watchlist (deduplicated)
WATCHLIST = list(set(list(WINDOW_STOCKS.keys()) + BIG_TECH))
WATCHLIST.sort()

# Polling interval (seconds) per timeframe
POLL_INTERVAL = {
    "30m": 60,
    "4h": 120,
    "daily": 300,
}

# Alert cooldown — don't repeat same signal within N bars
ALERT_COOLDOWN_BARS = 3

# ─── Market Hours (EST) ───────────────────────────────────────
MARKET_OPEN_HOUR = 9    # 9:30 AM EST
MARKET_OPEN_MIN = 30
MARKET_CLOSE_HOUR = 16  # 4:00 PM EST
MARKET_CLOSE_MIN = 0

# Pre-market start (launch OpenD this early)
PRE_MARKET_HOUR = 9
PRE_MARKET_MIN = 0

# Post-market summary time
POST_MARKET_HOUR = 16
POST_MARKET_MIN = 30
