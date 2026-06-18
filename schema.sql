-- =============================================================
-- WHALE TRACKER :: Supabase / PostgreSQL schema
-- Run this in the Supabase SQL Editor before first pipeline run.
-- =============================================================

create extension if not exists "pgcrypto";

create table if not exists public.whale_transactions (
    id                  uuid primary key default gen_random_uuid(),
    tx_hash             text unique,                 -- idempotency key (dedupe re-runs)
    timestamp           timestamptz not null,
    from_address        text not null,
    to_address          text not null,
    value_usdt          numeric not null,
    eth_price           numeric,                     -- ETH/USDT close at T=0
    eth_price_1h_later  numeric,                     -- ETH/USDT close at T+1H
    price_change_1h_pct numeric,
    time_of_day         text check (time_of_day in ('Morning','Afternoon','Evening','Night')),
    entity_category     text,                        -- EXCHANGE | DEX | SMART MONEY | TREASURY | UNKNOWN
    entity_name         text,
    ai_analysis         text,
    created_at          timestamptz default now()
);

create index if not exists idx_whale_tx_timestamp on public.whale_transactions ("timestamp" desc);
create index if not exists idx_whale_tx_category  on public.whale_transactions (entity_category);

-- Frontend reads with the anon key -> allow public SELECT only.
alter table public.whale_transactions enable row level security;

drop policy if exists "public read" on public.whale_transactions;
create policy "public read"
    on public.whale_transactions
    for select
    to anon
    using (true);

-- The Python pipeline writes with the SERVICE ROLE key, which bypasses RLS.
