"""Central configuration for the whale-tracker pipeline.

Reads everything from environment variables (.env supported via python-dotenv)
so no secrets ever live in source control.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# ── API keys / endpoints ─────────────────────────────────────────────────────
ETHERSCAN_API_KEY: str = os.getenv("ETHERSCAN_API_KEY", "")
ETHERSCAN_BASE_URL: str = "https://api.etherscan.io/v2/api"   # V2 unified endpoint
CHAIN_ID: int = 1                                             # Ethereum mainnet

BINANCE_BASE_URL: str = "https://api.binance.com"

SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
# Use the SERVICE ROLE key here (server-side only!) so inserts bypass RLS.
SUPABASE_SERVICE_KEY: str = os.getenv("SUPABASE_SERVICE_KEY", "")

# ── Token / market constants ────────────────────────────────────────────────
USDT_CONTRACT: str = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
USDT_DECIMALS: int = 6

KLINE_SYMBOL: str = "ETHUSDT"
KLINE_INTERVAL: str = "5m"
KLINE_LIMIT: int = 1000          # 1000 × 5m ≈ 83 hours of price context

# ── Pipeline knobs ──────────────────────────────────────────────────────────
MIN_TRANSFER_USD: float = 1_000_000      # whale threshold
SMART_MONEY_THRESHOLD_USD: float = 5_000_000
MAX_TRANSFERS: int = 10_000              # Etherscan hard cap per (page*offset)
PAGE_SIZE: int = 1_000                   # 10 pages × 1000 rows

# Shift tx timestamps back 24h so the T+1H "future" candle always exists
# inside our kline window. Set to False for true live mode (rows whose
# T+1H candle hasn't printed yet will carry NULL future prices).
SIMULATE_HISTORY: bool = True
SIMULATION_SHIFT_HOURS: int = 24

# merge_asof tolerances
T0_TOLERANCE = "10min"        # backward match: tx time -> last closed candle
T1H_TOLERANCE = "10min"       # nearest match around the T+1H target

REQUEST_TIMEOUT: int = 30
ETHERSCAN_RATE_SLEEP: float = 0.25       # 5 req/s free-tier safety margin


def validate() -> None:
    missing = [
        name
        for name, val in {
            "ETHERSCAN_API_KEY": ETHERSCAN_API_KEY,
            "SUPABASE_URL": SUPABASE_URL,
            "SUPABASE_SERVICE_KEY": SUPABASE_SERVICE_KEY,
        }.items()
        if not val
    ]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}. "
            "Copy .env.example to .env and fill them in."
        )
