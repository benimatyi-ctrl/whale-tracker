"""main.py — pipeline orchestrator.

    Etherscan (10k USDT transfers > $1M)
        -> Binance 5m klines
        -> double merge_asof price enrichment
        -> entity tagging
        -> quant narrative engine
        -> Supabase upsert

Run:  python main.py
"""

from __future__ import annotations

import logging
import sys

import pandas as pd

import analyst
import config
import database
import entity_tagger
import fetcher
import processor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-9s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")


def run() -> int:
    config.validate()

    # 1 ── Whale transfers ---------------------------------------------------
    log.info("STEP 1/5  Fetching USDT whale transfers from Etherscan ...")
    tx_df = fetcher.fetch_whale_transfers()
    if tx_df.empty:
        log.error("No qualifying transfers found — aborting.")
        return 1

    # 2 ── Price tape ----------------------------------------------------------
    log.info("STEP 2/5  Fetching ETH/USDT 5m klines from Binance ...")
    klines = processor.fetch_eth_klines()

    # 3 ── Double merge_asof enrichment ---------------------------------------
    log.info("STEP 3/5  Matching transactions to T=0 / T+1H candles ...")
    tx_df = processor.enrich_with_prices(tx_df, klines)

    # Keep only rows that landed inside the kline window at T=0.
    before = len(tx_df)
    tx_df = tx_df.dropna(subset=["eth_price"]).reset_index(drop=True)
    log.info("Kept %d/%d rows inside the price window.", len(tx_df), before)
    if tx_df.empty:
        log.error("No transactions matched the kline window — try SIMULATE_HISTORY=True.")
        return 1

    # 4 ── Entity tagging + narrative engine ----------------------------------
    log.info("STEP 4/5  Tagging entities & generating quant narratives ...")
    tags = tx_df.apply(
        lambda r: entity_tagger.tag_transaction(
            r["from_address"], r["to_address"], r["value_usdt"]
        ),
        axis=1,
    )
    tx_df = pd.concat([tx_df, pd.DataFrame(list(tags), index=tx_df.index)], axis=1)
    tx_df = analyst.annotate_dataframe(tx_df, klines)

    # 5 ── Persist -------------------------------------------------------------
    log.info("STEP 5/5  Saving to Supabase ...")
    saved = database.save_transactions(tx_df)

    log.info("Pipeline complete — %d rows persisted.", saved)
    log.info("Sample narrative:\n%s", tx_df.iloc[0]["ai_analysis"])
    return 0


if __name__ == "__main__":
    sys.exit(run())
