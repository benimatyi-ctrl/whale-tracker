"""database.py — persists the enriched DataFrame to Supabase.

Uses supabase-py with the SERVICE ROLE key (server-side only). Upserts on
tx_hash so re-running the pipeline is idempotent. NaN/NaT are converted to
None because PostgREST rejects JSON NaN.
"""

from __future__ import annotations

import logging
import math

import pandas as pd
from supabase import Client, create_client

import config

log = logging.getLogger("database")

CHUNK = 500

COLUMNS = [
    "tx_hash", "timestamp", "from_address", "to_address", "value_usdt",
    "eth_price", "eth_price_1h_later", "price_change_1h_pct",
    "time_of_day", "entity_category", "entity_name", "ai_analysis",
]


def get_client() -> Client:
    return create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_KEY)


def _clean(value):
    """JSON-safe scalar: NaN/NaT -> None, Timestamps -> ISO strings."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        ts = value if value.tzinfo else value.tz_localize("UTC")
        return ts.isoformat()
    if pd.isna(value):
        return None
    return value


def save_transactions(df: pd.DataFrame) -> int:
    if df.empty:
        log.warning("Nothing to save.")
        return 0

    client = get_client()
    records = [
        {col: _clean(row[col]) for col in COLUMNS if col in row.index}
        for _, row in df.iterrows()
    ]

    saved = 0
    for i in range(0, len(records), CHUNK):
        chunk = records[i : i + CHUNK]
        resp = (
            client.table("whale_transactions")
            .upsert(chunk, on_conflict="tx_hash")
            .execute()
        )
        saved += len(resp.data or chunk)
        log.info("Upserted rows %d-%d.", i + 1, i + len(chunk))

    log.info("Saved %d rows to Supabase.", saved)
    return saved
