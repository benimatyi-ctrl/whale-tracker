import React from "react";
import {
  Area,
  AreaChart,
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";

/* ── Shared palette / helpers ───────────────────────────────────────── */
const C = {
  up: "#10b981",
  upDim: "#065f46",
  down: "#ef4444",
  grid: "#1c1c20",
  axis: "#52525b",
  text: "#a1a1aa",
  price: "#fbbf24",
  bar: "#10b981",
};

const fmtUsd = (v) =>
  v >= 1e9 ? `$${(v / 1e9).toFixed(1)}B`
  : v >= 1e6 ? `$${(v / 1e6).toFixed(1)}M`
  : v >= 1e3 ? `$${(v / 1e3).toFixed(0)}K`
  : `$${Number(v).toFixed(0)}`;

const fmtTime = (iso) =>
  new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });

const axisProps = {
  stroke: C.axis,
  tick: { fill: C.text, fontSize: 10, fontFamily: "IBM Plex Mono, monospace" },
  tickLine: false,
  axisLine: { stroke: C.grid },
};

/* ── Terminal panel chrome ──────────────────────────────────────────── */
export function Panel({ code, title, right, children, className = "" }) {
  return (
    <div className={`border border-term-border bg-term-panel flex flex-col ${className}`}>
      <div className="flex items-center justify-between border-b border-term-border px-3 py-2">
        <div className="flex items-baseline gap-2">
          <span className="font-mono text-[10px] text-emerald-500">{code}</span>
          <span className="panel-title font-mono text-[11px] font-semibold uppercase text-zinc-300">
            {title}
          </span>
        </div>
        {right && <span className="font-mono text-[10px] text-zinc-500">{right}</span>}
      </div>
      <div className="flex-1 p-2">{children}</div>
    </div>
  );
}

function TermTooltip({ active, payload, label, rows }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="border border-term-border bg-[#09090b] px-3 py-2 font-mono text-[10px] shadow-xl">
      {label != null && <div className="mb-1 text-zinc-500">{label}</div>}
      {rows(payload).map(([k, v, color]) => (
        <div key={k} className="flex justify-between gap-4">
          <span className="text-zinc-400">{k}</span>
          <span style={{ color: color ?? "#e4e4e7" }}>{v}</span>
        </div>
      ))}
    </div>
  );
}

/* ════ 1. PRICE / VOLUME OVERLAY ════════════════════════════════════ */
export function PriceVolumeOverlayChart({ data }) {
  return (
    <Panel code="PVX" title="Whale Volume × ETH Price" right="1M BUCKETS" className="h-80">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 4, left: 4, bottom: 0 }}>
          <CartesianGrid stroke={C.grid} strokeDasharray="2 4" vertical={false} />
          <XAxis dataKey="minute" {...axisProps} tickFormatter={fmtTime} minTickGap={48} />
          <YAxis yAxisId="vol" orientation="left" {...axisProps} tickFormatter={fmtUsd} width={52} />
          <YAxis
            yAxisId="price"
            orientation="right"
            domain={["auto", "auto"]}
            {...axisProps}
            tickFormatter={(v) => `$${Number(v).toFixed(0)}`}
            width={56}
          />
          <Tooltip
            content={
              <TermTooltip
                rows={(p) =>
                  p.map((s) => [
                    s.dataKey === "volume" ? "WHALE VOL" : "ETH",
                    s.dataKey === "volume" ? fmtUsd(s.value) : `$${Number(s.value).toFixed(2)}`,
                    s.dataKey === "volume" ? C.up : C.price,
                  ])
                }
              />
            }
            labelFormatter={fmtTime}
            cursor={{ fill: "rgba(255,255,255,0.03)" }}
          />
          <Bar yAxisId="vol" dataKey="volume" fill={C.bar} fillOpacity={0.55} maxBarSize={10} />
          <Line
            yAxisId="price"
            type="monotone"
            dataKey="price"
            stroke={C.price}
            strokeWidth={1.5}
            dot={false}
            connectNulls
          />
        </ComposedChart>
      </ResponsiveContainer>
    </Panel>
  );
}

/* ════ 2. WIN / LOSS DONUT ══════════════════════════════════════════ */
export function WinLossDonutChart({ wins, losses }) {
  const total = wins + losses;
  const winRate = total ? (wins / total) * 100 : 0;
  const data = [
    { name: "WIN", value: wins },
    { name: "LOSS", value: losses },
  ];
  return (
    <Panel code="WLR" title="T+1H Win / Loss" right={`N=${total}`} className="h-80">
      <div className="relative h-full">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={data}
              dataKey="value"
              innerRadius="68%"
              outerRadius="88%"
              startAngle={90}
              endAngle={-270}
              paddingAngle={2}
              stroke="none"
            >
              <Cell fill={C.up} />
              <Cell fill={C.down} fillOpacity={0.8} />
            </Pie>
            <Tooltip
              content={
                <TermTooltip
                  rows={(p) =>
                    p.map((s) => [s.name, `${s.value} prints`, s.name === "WIN" ? C.up : C.down])
                  }
                />
              }
            />
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className={`font-mono text-3xl font-bold ${winRate >= 50 ? "text-emerald-400" : "text-red-400"}`}>
            {winRate.toFixed(1)}%
          </span>
          <span className="font-mono text-[10px] tracking-widest text-zinc-500">WIN RATE</span>
          <span className="mt-1 font-mono text-[10px] text-zinc-600">
            {wins}W / {losses}L
          </span>
        </div>
      </div>
    </Panel>
  );
}

/* ════ 3. IMPACT × SIZE SCATTER ═════════════════════════════════════ */
export function ImpactSizeScatterChart({ data }) {
  return (
    <Panel code="IMP" title="Trade Size × Price Impact" right="LOG-X" className="h-80">
      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ top: 8, right: 8, left: 4, bottom: 0 }}>
          <CartesianGrid stroke={C.grid} strokeDasharray="2 4" />
          <XAxis
            dataKey="size"
            type="number"
            scale="log"
            domain={["auto", "auto"]}
            {...axisProps}
            tickFormatter={fmtUsd}
            name="Size"
          />
          <YAxis
            dataKey="impact"
            type="number"
            {...axisProps}
            tickFormatter={(v) => `${v.toFixed(1)}%`}
            width={48}
            name="Impact"
          />
          <ZAxis dataKey="size" range={[18, 220]} />
          <ReferenceLine y={0} stroke={C.axis} strokeDasharray="4 4" />
          <Tooltip
            content={
              <TermTooltip
                rows={(p) => {
                  const d = p[0]?.payload ?? {};
                  return [
                    ["SIZE", fmtUsd(d.size)],
                    ["T+1H", `${d.impact >= 0 ? "+" : ""}${Number(d.impact).toFixed(2)}%`, d.impact >= 0 ? C.up : C.down],
                    ["ENTITY", d.entity ?? "—"],
                  ];
                }}
              />
            }
            cursor={{ strokeDasharray: "3 3", stroke: C.axis }}
          />
          <Scatter data={data} fillOpacity={0.7}>
            {data.map((d, i) => (
              <Cell key={i} fill={d.impact >= 0 ? C.up : C.down} />
            ))}
          </Scatter>
        </ScatterChart>
      </ResponsiveContainer>
    </Panel>
  );
}

/* ════ 4. CUMULATIVE IMPACT (EQUITY CURVE) ══════════════════════════ */
export function CumulativeImpactCurve({ data }) {
  const last = data.length ? data[data.length - 1].cum : 0;
  const positive = last >= 0;
  return (
    <Panel
      code="EQC"
      title="Cumulative T+1H Impact"
      right={
        <span className={positive ? "text-emerald-400" : "text-red-400"}>
          {positive ? "+" : ""}
          {last.toFixed(2)}%
        </span>
      }
      className="h-80"
    >
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 8, left: 4, bottom: 0 }}>
          <defs>
            <linearGradient id="eqFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={positive ? C.up : C.down} stopOpacity={0.32} />
              <stop offset="100%" stopColor={positive ? C.up : C.down} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke={C.grid} strokeDasharray="2 4" vertical={false} />
          <XAxis dataKey="time" {...axisProps} tickFormatter={fmtTime} minTickGap={48} />
          <YAxis {...axisProps} tickFormatter={(v) => `${v.toFixed(1)}%`} width={48} />
          <ReferenceLine y={0} stroke={C.axis} strokeDasharray="4 4" />
          <Tooltip
            content={
              <TermTooltip
                rows={(p) => [["CUM IMPACT", `${Number(p[0]?.value).toFixed(2)}%`, p[0]?.value >= 0 ? C.up : C.down]]}
              />
            }
            labelFormatter={fmtTime}
          />
          <Area
            type="monotone"
            dataKey="cum"
            stroke={positive ? C.up : C.down}
            strokeWidth={1.5}
            fill="url(#eqFill)"
            dot={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </Panel>
  );
}
