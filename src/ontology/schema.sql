-- Meridian ontology layer (physical tables) built from raw `deals` + `reps`.
-- This is intentionally simple for the take-home: stable PKs + FKs, with raw tables
-- still available as reference.

CREATE TABLE IF NOT EXISTS onto_region (
  region_id TEXT PRIMARY KEY,
  region_name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS onto_segment (
  segment_id TEXT PRIMARY KEY,
  segment_name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS onto_account (
  account_id TEXT PRIMARY KEY,
  account_name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS onto_manager (
  manager_id TEXT PRIMARY KEY,
  manager_name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS onto_rep (
  rep_id TEXT PRIMARY KEY,
  rep_name TEXT NOT NULL,
  hire_date DATE,
  manager_id TEXT REFERENCES onto_manager(manager_id),
  region_id TEXT REFERENCES onto_region(region_id),
  segment_id TEXT REFERENCES onto_segment(segment_id)
);

-- Backwards-compatible migrations (for existing DBs).
ALTER TABLE IF EXISTS onto_rep
  ADD COLUMN IF NOT EXISTS manager_id TEXT;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'fk_onto_rep_manager'
  ) THEN
    ALTER TABLE onto_rep
      ADD CONSTRAINT fk_onto_rep_manager
      FOREIGN KEY (manager_id) REFERENCES onto_manager(manager_id);
  END IF;
END $$;

-- Normalize quota into a time series table (instead of a per-quarter column).
CREATE TABLE IF NOT EXISTS onto_rep_quota (
  rep_id TEXT NOT NULL REFERENCES onto_rep(rep_id) ON DELETE CASCADE,
  period TEXT NOT NULL, -- e.g. "2026Q1"
  quota NUMERIC NOT NULL,
  PRIMARY KEY (rep_id, period)
);

-- Fact table; retains raw fields but keys are explicit FKs.
CREATE TABLE IF NOT EXISTS onto_deal (
  deal_id TEXT PRIMARY KEY,
  account_id TEXT REFERENCES onto_account(account_id),
  rep_id TEXT REFERENCES onto_rep(rep_id),
  region_id TEXT REFERENCES onto_region(region_id),
  segment_id TEXT REFERENCES onto_segment(segment_id),

  stage TEXT,
  deal_value NUMERIC,
  close_date DATE,
  created_date DATE,
  product_line TEXT,
  loss_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_onto_deal_rep ON onto_deal(rep_id);
CREATE INDEX IF NOT EXISTS idx_onto_deal_account ON onto_deal(account_id);
CREATE INDEX IF NOT EXISTS idx_onto_deal_close_date ON onto_deal(close_date);

