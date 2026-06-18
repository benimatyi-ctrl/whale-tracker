"""entity_tagger.py — heuristic on-chain entity attribution.

Checks an address against a curated map of known hot wallets (CEXes, DEX
routers/pools, the Tether Treasury). Unknown addresses moving institutional
size (>= $5M) get promoted to SMART MONEY — nobody moves eight figures of
USDT by accident.
"""

from __future__ import annotations

import config

# All keys lowercase — Ethereum addresses are case-insensitive
# (EIP-55 checksum is only a display convention).
KNOWN_ENTITIES: dict[str, tuple[str, str]] = {
    # ── Binance ──────────────────────────────────────────────────────────
    "0x28c6c06298d514db089934071355e5743bf21d60": ("EXCHANGE", "Binance 14"),
    "0x21a31ee1afc51d94c2efccaa2092ad1028285549": ("EXCHANGE", "Binance 15"),
    "0xdfd5293d8e347dfe59e90efd55b2956a1343963d": ("EXCHANGE", "Binance 16"),
    "0x56eddb7aa87536c09ccc2793473599fd21a8b17f": ("EXCHANGE", "Binance 17"),
    "0x9696f59e4d72e237be84ffd425dcad154bf96976": ("EXCHANGE", "Binance 18"),
    "0xf977814e90da44bfa03b6295a0616a897441acec": ("EXCHANGE", "Binance 8"),
    "0x5a52e96bacdabb82fd05763e25335261b270efcb": ("EXCHANGE", "Binance 28"),
    "0xbe0eb53f46cd790cd13851d5eff43d12404d33e8": ("EXCHANGE", "Binance 7"),
    # ── Kraken ───────────────────────────────────────────────────────────
    "0x2910543af39aba0cd09dbb2d50200b3e800a63d2": ("EXCHANGE", "Kraken 1"),
    "0x0a869d79a7052c7f1b55a8ebabbea3420f0d1e13": ("EXCHANGE", "Kraken 2"),
    "0xe853c56864a2ebe4576a807d26fdc4a0ada51919": ("EXCHANGE", "Kraken 3"),
    "0x267be1c1d684f78cb4f6a176c4911b741e4ffdc0": ("EXCHANGE", "Kraken 4"),
    # ── Coinbase ─────────────────────────────────────────────────────────
    "0x71660c4005ba85c37ccec55d0c4493e66fe775d3": ("EXCHANGE", "Coinbase 1"),
    "0x503828976d22510aad0201ac7ec88293211d23da": ("EXCHANGE", "Coinbase 2"),
    "0xddfabcdc4d8ffc6d5beaf154f18b778f892a0740": ("EXCHANGE", "Coinbase 3"),
    "0xa9d1e08c7793af67e9d92fe308d5697fb81d3e43": ("EXCHANGE", "Coinbase 10"),
    # ── OKX ──────────────────────────────────────────────────────────────
    "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b": ("EXCHANGE", "OKX"),
    "0x98ec059dc3adfbdd63429454aeb0c990fba4a128": ("EXCHANGE", "OKX 2"),
    "0x5041ed759dd4afc3a72b8192c143f72f4724081a": ("EXCHANGE", "OKX 7"),
    # ── Bybit / Bitfinex / HTX / Crypto.com ─────────────────────────────
    "0xf89d7b9c864f589bbf53a82105107622b35eaa40": ("EXCHANGE", "Bybit"),
    "0x77134cbc06cb00b66f4c7e623d5fdbf6777635ec": ("EXCHANGE", "Bitfinex 19"),
    "0x1151314c646ce4e0efd76d1af4760ae66a9fe30f": ("EXCHANGE", "Bitfinex 4"),
    "0xab5c66752a9e8167967685f1450532fb96d5d24f": ("EXCHANGE", "HTX 1"),
    "0x46340b20830761efd32832a74d7169b29feb9758": ("EXCHANGE", "Crypto.com 2"),
    "0x6262998ced04146fa42253a5c0af90ca02dfd2a3": ("EXCHANGE", "Crypto.com"),
    # ── DEX infrastructure ───────────────────────────────────────────────
    "0xe592427a0aece92de3edee1f18e0157c05861564": ("DEX", "Uniswap V3 Router"),
    "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45": ("DEX", "Uniswap V3 Router 2"),
    "0x3fc91a3afd70395cd496c647d5a6cc9d4b2b7fad": ("DEX", "Uniswap Universal Router"),
    "0x7a250d5630b4cf539739df2c5dacb4c659f2488d": ("DEX", "Uniswap V2 Router"),
    "0x11b815efb8f581194ae79006d24e0d814b7697f6": ("DEX", "Uniswap V3: ETH/USDT"),
    "0x0d4a11d5eeaac28ec3f61d100daf4d40471f1852": ("DEX", "Uniswap V2: ETH/USDT"),
    "0xbebc44782c7db0a1a60cb6fe97d0b483032ff1c7": ("DEX", "Curve 3pool"),
    "0xd51a44d3fae010294c616388b506acda1bfaae46": ("DEX", "Curve Tricrypto"),
    "0x1111111254eeb25477b68fb85ed929f73a960582": ("DEX", "1inch Router"),
    "0xdef1c0ded9bec7f1a1670819833240f027b25eff": ("DEX", "0x Exchange Proxy"),
    # ── Treasury / issuers ───────────────────────────────────────────────
    "0x5754284f345afc66a98fbb0a0afe71e0f007b949": ("TREASURY", "Tether Treasury"),
    "0xc6cde7c39eb2f0f0095f41570af89efc2c1ea828": ("TREASURY", "Tether Multisig"),
}


def tag_address(address: str, value_usd: float) -> tuple[str, str]:
    """Return (entity_category, entity_name) for an address.

    Heuristic ladder:
      1. exact match against the known-entity map;
      2. unknown + value >= $5M  -> SMART MONEY ("Inst. Whale");
      3. otherwise               -> UNKNOWN ("Unknown Wallet").
    """
    if not address:
        return "UNKNOWN", "Unknown Wallet"

    hit = KNOWN_ENTITIES.get(address.strip().lower())
    if hit:
        return hit

    if value_usd >= config.SMART_MONEY_THRESHOLD_USD:
        return "SMART MONEY", "Inst. Whale"

    return "UNKNOWN", "Unknown Wallet"


def tag_transaction(from_address: str, to_address: str, value_usd: float) -> dict:
    """Tag both legs of a transfer and pick the *dominant* entity for the row.

    Priority for the headline entity: the known/most-informative side wins —
    TREASURY > DEX > EXCHANGE > SMART MONEY > UNKNOWN. The full per-leg
    attribution is kept for the analysis engine (flow direction matters).
    """
    from_cat, from_name = tag_address(from_address, value_usd)
    to_cat, to_name = tag_address(to_address, value_usd)

    priority = {"TREASURY": 4, "DEX": 3, "EXCHANGE": 2, "SMART MONEY": 1, "UNKNOWN": 0}
    if priority[from_cat] >= priority[to_cat]:
        category, name = from_cat, from_name
    else:
        category, name = to_cat, to_name

    return {
        "entity_category": category,
        "entity_name": name,
        "from_category": from_cat,
        "from_name": from_name,
        "to_category": to_cat,
        "to_name": to_name,
    }
