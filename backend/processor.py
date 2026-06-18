"""processor.py — joins whale transfers to ETH/USDT price action.

The core of this module is the **double merge_asof**:

    1. T=0   : match each transaction to the most recent *closed* 5m candle
               (direction='backward') -> eth_price
    2. T+1H  : compute target = tx_time + 1h, match to the candle *nearest*
               that target (direction='nearest') -> eth_price_1h_later

merge_asof is full of silent-NaN landmines, so we are strict about three
things:

    * every datetime key is normalized to tz-naive ``datetime64[ns]`` (UTC
      wall-clock) — mixing tz-aware and tz-naive keys raises, and mixing
      ns/us resolutions silently mis-matches;
    * both sides are sorted on the join key *immediately before* each merge
      (merge_asof requires sorted keys);
    * the caller's original row order is preserved via a `_row` cursor and
      restored at the end, so downstream code never sees a shuffled frame.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import requests

import config

log = logging.getLogger("processor")

ONE_HOUR = pd.Timedelta(hours=1)


# ─────────────────────────────────────────────────────────────────────────────
# Klines
# ─────────────────────────────────────────────────────────────────────────────
def fetch_eth_klines() -> pd.DataFrame:
    """Last `KLINE_LIMIT` closed 5m ETH/USDT candles from Binance.

    Returns columns: open_time (tz-naive datetime64[ns], UTC wall-clock),
    close (float), volume (float), sorted ascending.
    """
    url = f"{config.BINANCE_BASE_URL}/api/v3/klines"
    resp = requests.get(
        url,
        params={
            "symbol": config.KLINE_SYMBOL,
            "interval": config.KLINE_INTERVAL,
            "limit": config.KLINE_LIMIT,
        },
        timeout=config.REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    raw = resp.json()

    klines = pd.DataFrame(
        raw,
        columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades",
            "taker_buy_base", "taker_buy_quote", "ignore",
        ],
    )
    # ms epoch -> tz-naive ns datetime (UTC wall clock). No tz attached on
    # purpose: every join key in this module is naive-UTC.
    klines["open_time"] = pd.to_datetime(
        pd.to_numeric(klines["open_time"]), unit="ms"
    ).astype("datetime64[ns]")
    for col in ("open", "high", "low", "close", "volume"):
        klines[col] = pd.to_numeric(klines[col], errors="coerce")

    klines = (
        klines[["open_time", "open", "high", "low", "close", "volume"]]
        .dropna()
        .sort_values("open_time")
        .reset_index(drop=True)
    )
    log.info(
        "Fetched %d klines: %s -> %s",
        len(klines), klines["open_time"].iloc[0], klines["open_time"].iloc[-1],
    )
    return klines


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _to_naive_ns(series: pd.Series) -> pd.Series:
    """Normalize any datetime series to tz-naive datetime64[ns] (UTC)."""
    s = pd.to_datetime(series, utc=True)   # force tz-aware UTC first
    s = s.dt.tz_localize(None)             # drop tz -> naive UTC wall clock
    return s.astype("datetime64[ns]")      # force ns resolution (pandas 2.x!)


def classify_time_of_day(ts: pd.Series) -> pd.Series:
    """UTC-hour session buckets: Morning 6-12, Afternoon 12-18, Evening 18-24, Night 0-6."""
    hours = ts.dt.hour
    return pd.cut(
        hours,
        bins=[-1, 5, 11, 17, 23],
        labels=["Night", "Morning", "Afternoon", "Evening"],
    ).astype(str)


# ─────────────────────────────────────────────────────────────────────────────
# The double merge_asof
# ─────────────────────────────────────────────────────────────────────────────
def enrich_with_prices(tx_df: pd.DataFrame, klines: pd.DataFrame) -> pd.DataFrame:
    """Attach eth_price (T=0), eth_price_1h_later (T+1H) and the % change."""
    if tx_df.empty:
        return tx_df.assign(
            eth_price=np.nan, eth_price_1h_later=np.nan,
            price_change_1h_pct=np.nan, time_of_day=None,
        )

    df = tx_df.copy()

    # Original-order cursor: merge_asof forces sorts, we undo them at the end.
    df["_row"] = np.arange(len(df))

    # Normalize ALL join keys to tz-naive datetime64[ns].
    df["match_time"] = _to_naive_ns(df["timestamp"])
    k = klines.copy()
    k["open_time"] = _to_naive_ns(k["open_time"])
    k = k.sort_values("open_time").reset_index(drop=True)

    # Optional simulation: rewind tx times 24h so T+1H is always in-window.
    if config.SIMULATE_HISTORY:
        df["match_time"] -= pd.Timedelta(hours=config.SIMULATION_SHIFT_HOURS)
        log.info("Simulation mode ON: tx times shifted back %dh.",
                 config.SIMULATION_SHIFT_HOURS)

    # ── Merge #1 — T=0, direction='backward' ──────────────────────────────
    # "What was the last candle that had already opened when the tx landed?"
    df = df.sort_values("match_time", kind="mergesort")
    df = pd.merge_asof(
        df,
        k[["open_time", "close"]].rename(columns={"close": "eth_price"}),
        left_on="match_time",
        right_on="open_time",
        direction="backward",
        tolerance=pd.Timedelta(config.T0_TOLERANCE),
    ).drop(columns=["open_time"])

    # ── Merge #2 — T+1H, direction='nearest' ──────────────────────────────
    # Target the candle closest to tx_time + 1h (nearest absorbs the ±2.5m
    # phase offset between an arbitrary timestamp and the 5m grid).
    df["target_1h"] = df["match_time"] + ONE_HOUR
    df = df.sort_values("target_1h", kind="mergesort")
    df = pd.merge_asof(
        df,
        k[["open_time", "close"]].rename(columns={"close": "eth_price_1h_later"}),
        left_on="target_1h",
        right_on="open_time",
        direction="nearest",
        tolerance=pd.Timedelta(config.T1H_TOLERANCE),
    ).drop(columns=["open_time"])

    # Guard: 'nearest' will happily snap a target that lies BEYOND the last
    # candle back onto stale data. If the T+1H target falls outside the kline
    # window (future not printed yet), the match is not a real 1h-later price.
    last_candle = k["open_time"].max()
    out_of_window = df["target_1h"] > last_candle + pd.Timedelta(config.T1H_TOLERANCE)
    df.loc[out_of_window, "eth_price_1h_later"] = np.nan

    # ── Derived fields ────────────────────────────────────────────────────
    df["price_change_1h_pct"] = (
        (df["eth_price_1h_later"] - df["eth_price"]) / df["eth_price"] * 100.0
    )
    df["time_of_day"] = classify_time_of_day(df["match_time"])

    # Restore the caller's row order and drop scaffolding.
    df = (
        df.sort_values("_row")
        .drop(columns=["_row", "target_1h"])
        .reset_index(drop=True)
    )

    matched = df["eth_price"].notna().sum()
    future = df["eth_price_1h_later"].notna().sum()
    log.info("Price match: %d/%d at T=0, %d/%d at T+1H.",
             matched, len(df), future, len(df))
    return df
