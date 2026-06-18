"""fetcher.py — pulls large USDT (ERC-20) transfers from Etherscan V2.

Strategy: page through `account.tokentx` for the USDT contract (newest first),
convert raw 6-decimal integer values to USD, and keep only transfers above the
whale threshold. Etherscan caps page*offset at 10,000 records, which is exactly
our budget.
"""

from __future__ import annotations

import logging
import time

import pandas as pd
import requests

import config

log = logging.getLogger("fetcher")


def _etherscan_get(params: dict) -> list[dict]:
    params = {
        "chainid": config.CHAIN_ID,
        "apikey": config.ETHERSCAN_API_KEY,
        **params,
    }
    resp = requests.get(
        config.ETHERSCAN_BASE_URL, params=params, timeout=config.REQUEST_TIMEOUT
    )
    resp.raise_for_status()
    payload = resp.json()

    # Etherscan returns status "0" both for errors AND for empty result sets.
    if payload.get("status") == "1":
        return payload.get("result", [])
    message = str(payload.get("message", ""))
    result = payload.get("result")
    if "No transactions found" in message or result == []:
        return []
    raise RuntimeError(f"Etherscan error: {message} / {result}")


def fetch_whale_transfers() -> pd.DataFrame:
    """Return a DataFrame of USDT transfers >= MIN_TRANSFER_USD.

    Columns: tx_hash, timestamp (tz-aware UTC), from_address, to_address,
    value_usdt (float USD).
    """
    rows: list[dict] = []
    pages = config.MAX_TRANSFERS // config.PAGE_SIZE

    for page in range(1, pages + 1):
        log.info("Etherscan page %d/%d ...", page, pages)
        batch = _etherscan_get(
            {
                "module": "account",
                "action": "tokentx",
                "contractaddress": config.USDT_CONTRACT,
                "page": page,
                "offset": config.PAGE_SIZE,
                "sort": "desc",  # newest first
            }
        )
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < config.PAGE_SIZE:
            break
        time.sleep(config.ETHERSCAN_RATE_SLEEP)

    if not rows:
        log.warning("Etherscan returned zero transfers.")
        return pd.DataFrame(
            columns=["tx_hash", "timestamp", "from_address", "to_address", "value_usdt"]
        )

    df = pd.DataFrame(rows)

    # Raw value is an integer string with 6 implied decimals (USDT).
    df["value_usdt"] = (
        pd.to_numeric(df["value"], errors="coerce") / (10**config.USDT_DECIMALS)
    )
    df["timestamp"] = pd.to_datetime(
        pd.to_numeric(df["timeStamp"], errors="coerce"), unit="s", utc=True
    )

    df = df.rename(columns={"hash": "tx_hash", "from": "from_address", "to": "to_address"})
    df = df[["tx_hash", "timestamp", "from_address", "to_address", "value_usdt"]]

    df = df.dropna(subset=["value_usdt", "timestamp"])
    df = df[df["value_usdt"] >= config.MIN_TRANSFER_USD]

    # One ERC-20 tx can emit several Transfer logs; keep the largest per hash.
    df = (
        df.sort_values("value_usdt", ascending=False)
        .drop_duplicates(subset="tx_hash", keep="first")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    log.info("Kept %d whale transfers (>= $%.0f).", len(df), config.MIN_TRANSFER_USD)
    return df
