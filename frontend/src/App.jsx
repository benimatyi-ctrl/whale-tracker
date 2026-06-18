import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { supabase } from "./lib/supabase.js";
import {
  CumulativeImpactCurve,
  ImpactSizeScatterChart,
  Panel,
  PriceVolumeOverlayChart,
  WinLossDonutChart,
} from "./components/QuantCharts.jsx";

const POLL_MS = 30_000;
const ROW_LIMIT = 800;

/* ── Formatters ─────────────────────────────────────────────────────── */
const fmtUsd = (v) =>
  v >= 1e9 ? `$${(v / 1e9).toFixed(2)}B`
  : v >= 1e6 ? `$${(v / 1e6).toFixed(2)}M`
  : `$${Number(v ?? 0).toLocaleString()}`;

const fmtPct = (v) =>
  v == null ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(2)}%`;

const shortAddr = (a) => (a ? `${a.slice(0, 6)}…${a.slice(-4)}` : "—");

const fmtClock = (iso) =>
  new Date(iso).toLocaleString("en-GB", {
    month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });

/* ── Entity badge styling ───────────────────────────────────────────── */
const BADGE = {
  "SMART MONEY": "bg-emerald-500/10 text-emerald-400 border-emerald-500/40",
  DEX: "bg-purple-500/10 text-purple-400 border-purple-500/40",
  EXCHANGE: "bg-zinc-500/10 text-zinc-300 border-zinc-500/40",
  TREASURY: "bg-amber-500/10 text-amber-400 border-amber-500/40",
  UNKNOWN: "bg-zinc-800/40 text-zinc-500 border-zinc-700/60",
};

function EntityBadge({ category, name }) {
  const cls = BADGE[category] ?? BADGE.UNKNOWN;
  return (
    <div className="flex flex-col gap-0.5">
      <span className={`inline-block w-fit border px-1.5 py-0.5 font-mono text-[9px] font-semibold tracking-wider ${cls}`}>
        {category ?? "UNKNOWN"}
      </span>
      <span className="font-mono text-[10px] text-zinc-500">{name ?? "—"}</span>
    </div>
  );
}

/* ── Time-of-Day matrix card ────────────────────────────────────────── */
const SESSION_META = {
  Morning: { code: "MRN", hours: "06–12 UTC" },
  Afternoon: { code: "AFT", hours: "12–18 UTC" },
  Evening: { code: "EVE", hours: "18–24 UTC" },
  Night: { code: "NGT", hours: "00–06 UTC" },
};

function SessionCard({ session, stats }) {
  const meta = SESSION_META[session];
  const hasData = stats.n > 0;
  const winGood = stats.winRate >= 50;
  return (
    <div className="border border-term-border bg-term-panel p-3">
      <div className="flex items-baseline justify-between">
        <span className="font-mono text-[11px] font-semibold uppercase tracking-widest text-zinc-300">
          {session}
        </span>
        <span className="font-mono text-[9px] text-zinc-600">{meta.hours}</span>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2">
        <div>
          <div className="font-mono text-[9px] tracking-widest text-zinc-500">WIN PROB</div>
          <div className={`font-mono text-xl font-bold ${!hasData ? "text-zinc-600" : winGood ? "text-emerald-400" : "text-red-400"}`}>
            {hasData ? `${stats.winRate.toFixed(0)}%` : "—"}
          </div>
        </div>
        <div>
          <div className="font-mono text-[9px] tracking-widest text-zinc-500">AVG MOVE</div>
          <div className={`font-mono text-xl font-bold ${!hasData ? "text-zinc-600" : stats.avgMove >= 0 ? "text-emerald-400" : "text-red-400"}`}>
            {hasData ? fmtPct(stats.avgMove) : "—"}
          </div>
        </div>
      </div>
      <div className="mt-2 h-1 w-full bg-zinc-900">
        <div
          className={`h-1 ${winGood ? "bg-emerald-500" : "bg-red-500"}`}
          style={{ width: hasData ? `${Math.min(stats.winRate, 100)}%` : 0 }}
        />
      </div>
      <div className="mt-1 font-mono text-[9px] text-zinc-600">n={stats.n} prints</div>
    </div>
  );
}

/* ════════════════════════════════════════════════════════════════════ */
export default function App() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastSync, setLastSync] = useState(null);
  const [newIds, setNewIds] = useState(new Set());
  const [expanded, setExpanded] = useState(null);
  const knownIds = useRef(new Set());

  const fetchRows = useCallback(async () => {
    const { data, error: err } = await supabase
      .from("whale_transactions")
      .select("*")
      .order("timestamp", { ascending: false })
      .limit(ROW_LIMIT);

    if (err) {
      setError(err.message);
      setLoading(false);
      return;
    }
    const fresh = new Set(
      (data ?? []).filter((r) => knownIds.current.size && !knownIds.current.has(r.id)).map((r) => r.id)
    );
    (data ?? []).forEach((r) => knownIds.current.add(r.id));
    setNewIds(fresh);
    setRows(data ?? []);
    setError(null);
    setLastSync(new Date());
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchRows();
    const t = setInterval(fetchRows, POLL_MS);
    return () => clearInterval(t);
  }, [fetchRows]);

  /* ── All chart datasets derived in one memo ───────────────────────── */
  const derived = useMemo(() => {
    const labelled = rows.filter((r) => r.price_change_1h_pct != null);
    const asc = [...rows].sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));

    // 1) Overlay: aggregate whale volume per exact minute, avg price per minute
    const byMinute = new Map();
    for (const r of asc) {
      const minute = new Date(r.timestamp);
      minute.setSeconds(0, 0);
      const key = minute.toISOString();
      const slot = byMinute.get(key) ?? { minute: key, volume: 0, priceSum: 0, priceN: 0 };
      slot.volume += Number(r.value_usdt) || 0;
      if (r.eth_price != null) {
        slot.priceSum += Number(r.eth_price);
        slot.priceN += 1;
      }
      byMinute.set(key, slot);
    }
    const overlay = [...byMinute.values()].map((s) => ({
      minute: s.minute,
      volume: s.volume,
      price: s.priceN ? s.priceSum / s.priceN : null,
    }));

    // 2) Win/Loss
    const wins = labelled.filter((r) => r.price_change_1h_pct > 0).length;
    const losses = labelled.filter((r) => r.price_change_1h_pct <= 0).length;

    // 3) Scatter
    const scatter = labelled.map((r) => ({
      size: Number(r.value_usdt),
      impact: Number(r.price_change_1h_pct),
      entity: r.entity_name,
    }));

    // 4) Equity curve (chronological running sum of T+1H impact)
    let cum = 0;
    const equity = asc
      .filter((r) => r.price_change_1h_pct != null)
      .map((r) => {
        cum += Number(r.price_change_1h_pct);
        return { time: r.timestamp, cum };
      });

    // 5) Session matrix
    const sessions = {};
    for (const s of ["Morning", "Afternoon", "Evening", "Night"]) {
      const grp = labelled.filter((r) => r.time_of_day === s);
      const w = grp.filter((r) => r.price_change_1h_pct > 0).length;
      sessions[s] = {
        n: grp.length,
        winRate: grp.length ? (w / grp.length) * 100 : 0,
        avgMove: grp.length
          ? grp.reduce((a, r) => a + Number(r.price_change_1h_pct), 0) / grp.length
          : 0,
      };
    }

    // Header stats
    const totalVol = rows.reduce((a, r) => a + (Number(r.value_usdt) || 0), 0);
    const lastPrice = asc.length ? asc[asc.length - 1].eth_price : null;

    return { overlay, wins, losses, scatter, equity, sessions, totalVol, lastPrice };
  }, [rows]);

  /* ── Render ────────────────────────────────────────────────────────── */
  return (
    <div className="min-h-screen bg-term-bg px-4 py-4 lg:px-8">
      {/* HEADER */}
      <header className="mb-4 flex flex-wrap items-center justify-between gap-3 border border-term-border bg-term-panel px-4 py-3">
        <div className="flex items-center gap-3">
          <span className="live-dot inline-block h-2 w-2 bg-emerald-500" />
          <h1 className="font-mono text-sm font-bold tracking-[0.2em] text-zinc-100">
            WHALE<span className="text-emerald-500">/</span>TRX
          </h1>
          <span className="hidden font-mono text-[10px] text-zinc-600 sm:inline">
            INSTITUTIONAL USDT FLOW TERMINAL · ETH MAINNET
          </span>
        </div>
        <div className="flex items-center gap-6 font-mono text-[10px]">
          <div>
            <span className="text-zinc-500">PRINTS </span>
            <span className="text-zinc-200">{rows.length}</span>
          </div>
          <div>
            <span className="text-zinc-500">GROSS FLOW </span>
            <span className="text-emerald-400">{fmtUsd(derived.totalVol)}</span>
          </div>
          {derived.lastPrice != null && (
            <div>
              <span className="text-zinc-500">ETH </span>
              <span className="text-amber-400">${Number(derived.lastPrice).toFixed(2)}</span>
            </div>
          )}
          <div className="text-zinc-600">
            SYNC {lastSync ? lastSync.toLocaleTimeString("en-GB") : "—"}
          </div>
        </div>
      </header>

      {error && (
        <div className="mb-4 border border-red-500/40 bg-red-500/5 px-4 py-3 font-mono text-xs text-red-400">
          DATA LINK ERROR: {error} — check VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY and the RLS read policy.
        </div>
      )}

      {loading ? (
        <div className="flex h-64 items-center justify-center font-mono text-xs text-zinc-500">
          ESTABLISHING DATA LINK …
        </div>
      ) : (
        <>
          {/* TIME-OF-DAY MATRIX */}
          <section className="mb-4">
            <div className="mb-2 font-mono text-[10px] tracking-[0.2em] text-zinc-500">
              TIME-OF-DAY PRICE IMPACT MATRIX
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {["Morning", "Afternoon", "Evening", "Night"].map((s) => (
                <SessionCard key={s} session={s} stats={derived.sessions[s]} />
              ))}
            </div>
          </section>

          {/* QUANT CHARTS */}
          <section className="mb-4 grid grid-cols-1 gap-3 xl:grid-cols-2">
            <PriceVolumeOverlayChart data={derived.overlay} />
            <WinLossDonutChart wins={derived.wins} losses={derived.losses} />
            <ImpactSizeScatterChart data={derived.scatter} />
            <CumulativeImpactCurve data={derived.equity} />
          </section>

          {/* LEDGER */}
          <Panel code="LDG" title="Whale Transaction Ledger" right={`SHOWING ${rows.length}`}>
            <div className="max-h-[560px] overflow-auto">
              <table className="w-full border-collapse font-mono text-[11px]">
                <thead className="sticky top-0 z-10 bg-term-panel">
                  <tr className="border-b border-term-border text-left text-[9px] tracking-[0.15em] text-zinc-500">
                    <th className="px-2 py-2">TIME (UTC)</th>
                    <th className="px-2 py-2">ORIGIN ENTITY</th>
                    <th className="px-2 py-2">FROM</th>
                    <th className="px-2 py-2">TO</th>
                    <th className="px-2 py-2 text-right">VALUE</th>
                    <th className="px-2 py-2 text-right">ETH T=0</th>
                    <th className="px-2 py-2 text-right">T+1H Δ</th>
                    <th className="px-2 py-2">SESSION</th>
                    <th className="px-2 py-2 text-center">AI</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => {
                    const up = r.price_change_1h_pct != null && r.price_change_1h_pct > 0;
                    const isOpen = expanded === r.id;
                    return (
                      <React.Fragment key={r.id}>
                        <tr
                          className={`border-b border-term-border/60 hover:bg-zinc-900/60 ${
                            newIds.has(r.id) ? "row-new" : ""
                          }`}
                        >
                          <td className="px-2 py-2 whitespace-nowrap text-zinc-400">
                            {fmtClock(r.timestamp)}
                          </td>
                          <td className="px-2 py-2">
                            <EntityBadge category={r.entity_category} name={r.entity_name} />
                          </td>
                          <td className="px-2 py-2 text-zinc-500">{shortAddr(r.from_address)}</td>
                          <td className="px-2 py-2 text-zinc-500">{shortAddr(r.to_address)}</td>
                          <td className="px-2 py-2 text-right font-semibold text-zinc-100">
                            {fmtUsd(Number(r.value_usdt))}
                          </td>
                          <td className="px-2 py-2 text-right text-zinc-400">
                            {r.eth_price != null ? `$${Number(r.eth_price).toFixed(2)}` : "—"}
                          </td>
                          <td className={`px-2 py-2 text-right font-semibold ${
                            r.price_change_1h_pct == null ? "text-zinc-600" : up ? "text-emerald-400" : "text-red-400"
                          }`}>
                            {fmtPct(r.price_change_1h_pct)}
                          </td>
                          <td className="px-2 py-2 text-zinc-500">{r.time_of_day ?? "—"}</td>
                          <td className="px-2 py-2 text-center">
                            <button
                              onClick={() => setExpanded(isOpen ? null : r.id)}
                              className={`border px-1.5 py-0.5 text-[9px] tracking-wider ${
                                isOpen
                                  ? "border-emerald-500 bg-emerald-500/10 text-emerald-400"
                                  : "border-zinc-700 text-zinc-400 hover:border-emerald-500/60 hover:text-emerald-400"
                              }`}
                            >
                              {isOpen ? "CLOSE" : "VIEW"}
                            </button>
                          </td>
                        </tr>
                        {isOpen && (
                          <tr className="border-b border-term-border/60 bg-[#080809]">
                            <td colSpan={9} className="px-4 py-3">
                              <div className="mb-1 font-mono text-[9px] tracking-[0.2em] text-emerald-500">
                                QUANT DESK NOTE
                              </div>
                              <p className="max-w-5xl font-mono text-[11px] leading-relaxed text-zinc-300">
                                {r.ai_analysis ?? "No analysis available for this print."}
                              </p>
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    );
                  })}
                  {!rows.length && (
                    <tr>
                      <td colSpan={9} className="px-4 py-8 text-center text-zinc-600">
                        LEDGER EMPTY — run the Python pipeline to ingest whale flow.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </Panel>

          <footer className="mt-4 flex justify-between font-mono text-[9px] text-zinc-700">
            <span>DATA: ETHERSCAN · BINANCE · SUPABASE</span>
            <span>REFRESH {POLL_MS / 1000}s · NOT INVESTMENT ADVICE</span>
          </footer>
        </>
      )}
    </div>
  );
}
