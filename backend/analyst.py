"""analyst.py — the narrative engine ("the Brain").

Generates the `ai_analysis` text for every whale transfer. This is not a
template that restates the row; it is a small, deterministic quant model that
fuses six signals into a conviction score and a directional read:

  1. SIZE SHOCK        log-size z-score vs. the whole batch -> how abnormal
                       is this print relative to today's whale tape?
  2. FLOW VECTOR       entity-aware direction logic. USDT *leaving* an
                       exchange = dry powder withdrawn / OTC settlement
                       (risk-on bias). USDT *arriving* at an exchange =
                       ammunition staged on the order book (two-sided, lean
                       risk-on for stables: people deposit USDT to BUY).
                       Treasury mints/redeems = liquidity regime signal.
                       DEX legs = on-chain rotation, usually delta-neutral.
  3. ORDER-FLOW        VPIN-lite. Signed stablecoin imbalance (deposits minus
     TOXICITY          withdrawals) inside a ±30min window around the print.
                       One-sided clustering = informed flow; isolated prints
                       are usually noise (treasury ops, custody shuffles).
  4. IMPACT MODEL      square-root market-impact law:
                       E[|move|] = Y * sigma_1h * sqrt(Q / V_1h)
                       Compares the *realized* T+1H move to the model. Moves
                       far beyond model = over-extension -> mean-reversion
                       setup. Moves far below model with toxic flow =
                       absorption -> continuation risk.
  5. REGIME FILTER     lag-1 autocorrelation of 5m returns over the kline
                       window. AC > +0.05 = momentum tape (follow the flow),
                       AC < -0.05 = mean-reverting chop (fade extremes).
  6. SESSION EDGE      empirical win-rate and average T+1H move of the
                       transaction's own time-of-day bucket, computed from
                       the batch itself (no look-ahead beyond T+1H labels).

The six signals are blended into CONVICTION (0-100) and rendered as a short
desk-note: tag line, evidence, and an actionable conclusion with the
condition that would invalidate it.
"""

from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd

log = logging.getLogger("analyst")

TOXICITY_WINDOW = pd.Timedelta(minutes=30)
SQRT_IMPACT_Y = 0.8          # Almgren-style liquidity constant (conservative)
AC_MOMENTUM, AC_REVERSION = 0.05, -0.05


# ─────────────────────────────────────────────────────────────────────────────
# Batch-level context (computed once)
# ─────────────────────────────────────────────────────────────────────────────
def build_market_context(df: pd.DataFrame, klines: pd.DataFrame) -> dict:
    """Pre-compute everything the per-row engine needs."""
    ctx: dict = {}

    # Realized volatility & regime from the 5m tape
    closes = klines["close"].astype(float)
    rets = closes.pct_change().dropna()
    sigma_5m = float(rets.std()) if len(rets) > 10 else 0.0
    ctx["sigma_1h"] = sigma_5m * math.sqrt(12)            # 12 × 5m = 1h
    ctx["autocorr"] = float(rets.autocorr(lag=1)) if len(rets) > 30 else 0.0

    if ctx["autocorr"] > AC_MOMENTUM:
        ctx["regime"] = "MOMENTUM"
    elif ctx["autocorr"] < AC_REVERSION:
        ctx["regime"] = "MEAN-REVERT"
    else:
        ctx["regime"] = "NEUTRAL"

    # 1h notional turnover proxy from kline volume (ETH units × price)
    if not klines.empty:
        notional_5m = (klines["volume"] * klines["close"]).astype(float)
        ctx["adv_1h_usd"] = float(notional_5m.tail(12).sum()) or 1.0
    else:
        ctx["adv_1h_usd"] = 1.0

    # Size distribution of the batch (log space — whale sizes are lognormal-ish)
    sizes = np.log10(df["value_usdt"].clip(lower=1.0))
    ctx["log_mu"] = float(sizes.mean())
    ctx["log_sd"] = float(sizes.std()) or 1e-9

    # Session edge table from the batch's own labelled outcomes
    labelled = df.dropna(subset=["price_change_1h_pct"])
    ctx["session_stats"] = {}
    for session, grp in labelled.groupby("time_of_day"):
        if len(grp) >= 3:
            ctx["session_stats"][session] = {
                "n": int(len(grp)),
                "win_rate": float((grp["price_change_1h_pct"] > 0).mean()),
                "avg_move": float(grp["price_change_1h_pct"].mean()),
            }

    # Pre-index signed flows for the toxicity window scan.
    # Sign convention: +1 = USDT deposited TO an exchange (buy ammunition),
    #                  -1 = USDT withdrawn FROM an exchange.
    flows = df[["timestamp", "value_usdt", "from_category", "to_category"]].copy()
    flows["sign"] = 0
    flows.loc[flows["to_category"] == "EXCHANGE", "sign"] = 1
    flows.loc[flows["from_category"] == "EXCHANGE", "sign"] = -1
    flows = flows[flows["sign"] != 0]
    ctx["signed_flows"] = flows.sort_values("timestamp")

    return ctx


# ─────────────────────────────────────────────────────────────────────────────
# Per-row signals
# ─────────────────────────────────────────────────────────────────────────────
def _size_shock(value_usd: float, ctx: dict) -> float:
    """z-score of log-size vs the batch."""
    return (math.log10(max(value_usd, 1.0)) - ctx["log_mu"]) / ctx["log_sd"]


def _flow_vector(row: pd.Series) -> tuple[int, str]:
    """(direction_bias, label). bias: +1 risk-on, -1 risk-off, 0 neutral."""
    f, t = row["from_category"], row["to_category"]

    if f == "TREASURY":
        return +1, "TREASURY ISSUANCE — fresh USDT entering circulation; net liquidity injection"
    if t == "TREASURY":
        return -1, "TREASURY REDEMPTION — USDT being retired; net liquidity drain"
    if f == "EXCHANGE" and t == "EXCHANGE":
        return 0, "INTER-EXCHANGE SHUFFLE — inventory rebalancing, typically informationless"
    if f == "EXCHANGE":
        return +1, "EXCHANGE OUTFLOW — stables pulled to self-custody/OTC; sell-side ammunition reduced"
    if t == "EXCHANGE":
        return +1, "EXCHANGE INFLOW — stablecoin ammunition staged on the book; historically precedes spot bids"
    if f == "DEX" or t == "DEX":
        return 0, "DEX ROTATION — on-chain liquidity migration, usually delta-neutral"
    return 0, "DARK FLOW — wallet-to-wallet transfer outside visible venues"


def _toxicity(row: pd.Series, ctx: dict) -> tuple[float, float]:
    """VPIN-lite: |imbalance| of signed exchange flow in ±30min around the print.

    Returns (toxicity 0..1, net_signed_usd). Toxicity ~1 means the window was
    completely one-sided — the signature of informed, urgent positioning.
    """
    flows = ctx["signed_flows"]
    if flows.empty:
        return 0.0, 0.0
    t0 = row["timestamp"]
    win = flows[(flows["timestamp"] >= t0 - TOXICITY_WINDOW)
                & (flows["timestamp"] <= t0 + TOXICITY_WINDOW)]
    if win.empty:
        return 0.0, 0.0
    signed = float((win["sign"] * win["value_usdt"]).sum())
    gross = float(win["value_usdt"].sum())
    return (abs(signed) / gross if gross > 0 else 0.0), signed


def _impact_model(row: pd.Series, ctx: dict) -> dict:
    """Square-root impact law vs realized move."""
    q = float(row["value_usdt"])
    expected = SQRT_IMPACT_Y * ctx["sigma_1h"] * math.sqrt(q / ctx["adv_1h_usd"]) * 100
    realized = row["price_change_1h_pct"]
    out = {"expected_pct": expected, "realized_pct": realized, "state": "UNLABELLED"}
    if pd.notna(realized) and expected > 0:
        ratio = abs(realized) / expected
        if ratio > 2.0:
            out["state"] = "OVER-EXTENDED"      # tape moved far beyond fair impact
        elif ratio < 0.5:
            out["state"] = "ABSORBED"           # size hit the book, price barely moved
        else:
            out["state"] = "IN-LINE"
        out["ratio"] = ratio
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Scoring + narrative
# ─────────────────────────────────────────────────────────────────────────────
def _conviction(size_z: float, bias: int, tox: float, session: dict | None,
                regime: str, impact: dict) -> int:
    """Blend signals into 0-100 conviction that the directional read pays."""
    score = 50.0
    score += float(np.clip(size_z * 8, -16, 16))            # abnormal size
    score += tox * 20 * (1 if bias != 0 else 0.3)           # one-sided urgency
    if session:
        score += (session["win_rate"] - 0.5) * 40           # session edge
    if regime == "MOMENTUM" and impact.get("state") == "ABSORBED":
        score += 8    # hidden absorption in a trending tape = continuation fuel
    if regime == "MEAN-REVERT" and impact.get("state") == "OVER-EXTENDED":
        score += 8    # stretched move in chop = fade setup
    if impact.get("state") == "IN-LINE":
        score -= 4    # nothing anomalous, fair pricing
    return int(np.clip(score, 1, 99))


def _fmt_usd(v: float) -> str:
    if v >= 1e9:
        return f"${v/1e9:.2f}B"
    if v >= 1e6:
        return f"${v/1e6:.1f}M"
    return f"${v/1e3:.0f}K"


def generate_analysis(row: pd.Series, ctx: dict) -> str:
    size_z = _size_shock(row["value_usdt"], ctx)
    bias, flow_label = _flow_vector(row)
    tox, net_flow = _toxicity(row, ctx)
    impact = _impact_model(row, ctx)
    session = ctx["session_stats"].get(row["time_of_day"])
    regime = ctx["regime"]
    conviction = _conviction(size_z, bias, tox, session, regime, impact)

    # ── Header tag ────────────────────────────────────────────────────────
    if tox > 0.75 and abs(net_flow) > 20e6:
        tag = "TOXIC FLOW ALERT"
    elif size_z > 1.5:
        tag = "OUTLIER PRINT"
    elif row["entity_category"] == "TREASURY":
        tag = "LIQUIDITY EVENT"
    elif impact.get("state") == "ABSORBED":
        tag = "STEALTH ABSORPTION"
    elif impact.get("state") == "OVER-EXTENDED":
        tag = "IMPACT OVERSHOOT"
    else:
        tag = "ROUTINE WHALE FLOW"

    parts: list[str] = [f"[{tag} | CONVICTION {conviction}/100]"]

    # ── Evidence ──────────────────────────────────────────────────────────
    pct = 100 * 0.5 * (1 + math.erf(size_z / math.sqrt(2)))
    parts.append(
        f"{_fmt_usd(row['value_usdt'])} print sits at the {pct:.0f}th percentile "
        f"of the current whale tape (log-z {size_z:+.2f}). {flow_label}."
    )

    if tox > 0.0:
        side = "deposit-heavy" if net_flow > 0 else "withdrawal-heavy"
        parts.append(
            f"Order-flow toxicity {tox:.2f} in the ±30min window "
            f"(net {_fmt_usd(abs(net_flow))} {side}) — "
            + ("one-sided clustering consistent with informed positioning."
               if tox > 0.6 else "two-sided tape, low information content.")
        )

    exp = impact["expected_pct"]
    if pd.notna(row["price_change_1h_pct"]):
        parts.append(
            f"Sqrt-impact model priced a ±{exp:.2f}% fair move for this size; "
            f"realized T+1H was {row['price_change_1h_pct']:+.2f}% "
            f"({impact['state']})."
        )
        if impact["state"] == "ABSORBED":
            parts.append(
                "Price barely reacted to institutional size — a resting "
                "counterparty is absorbing flow. In a "
                f"{regime.lower()} regime this is "
                + ("continuation fuel once the absorber steps away."
                   if regime == "MOMENTUM" else "typically range-defending behavior.")
            )
        elif impact["state"] == "OVER-EXTENDED":
            parts.append(
                f"Move ran {impact['ratio']:.1f}× beyond model impact — thin-book "
                "slippage, not flow-justified. Mean-reversion edge favors fading "
                "the extension back toward the pre-print level."
            )

    if session:
        parts.append(
            f"Session context: {row['time_of_day']} prints in this batch resolve "
            f"green {session['win_rate']*100:.0f}% of the time "
            f"(avg {session['avg_move']:+.2f}%, n={session['n']})."
        )

    # ── Actionable conclusion ─────────────────────────────────────────────
    if conviction >= 70:
        stance = "HIGH-CONVICTION"
    elif conviction >= 55:
        stance = "MODERATE"
    else:
        stance = "LOW-EDGE / STAND ASIDE"

    if bias > 0:
        direction = "risk-on lean — bid pullbacks toward the print-time level"
    elif bias < 0:
        direction = "risk-off lean — fade strength, tighten longs"
    else:
        direction = "no directional edge — treat as liquidity noise"

    invalidation = (
        "Invalidate if the next 30min flow window flips sign or toxicity decays below 0.3."
        if tox > 0.4 else
        "Re-arm only if follow-on prints cluster in the same direction."
    )
    parts.append(f"VERDICT: {stance}. {direction.capitalize()}. {invalidation} "
                 f"Tape regime: {regime} (lag-1 AC {ctx['autocorr']:+.3f}).")

    return " ".join(parts)


def annotate_dataframe(df: pd.DataFrame, klines: pd.DataFrame) -> pd.DataFrame:
    """Attach `ai_analysis` to every row."""
    if df.empty:
        return df.assign(ai_analysis=None)
    ctx = build_market_context(df, klines)
    log.info("Market context: regime=%s sigma_1h=%.3f%% adv_1h=%s",
             ctx["regime"], ctx["sigma_1h"] * 100, _fmt_usd(ctx["adv_1h_usd"]))
    df = df.copy()
    df["ai_analysis"] = df.apply(lambda r: generate_analysis(r, ctx), axis=1)
    return df
