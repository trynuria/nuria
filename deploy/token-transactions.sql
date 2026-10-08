-- Per-mint decoder state preserves earlier tests when the launch identity changes.
CREATE TABLE IF NOT EXISTS token_transactions (
  mint TEXT NOT NULL,
  signature TEXT NOT NULL,
  slot BIGINT NOT NULL,
  sequence BIGINT NOT NULL,
  status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  retry_after DOUBLE PRECISION NOT NULL DEFAULT 0,
  detail TEXT,
  PRIMARY KEY (mint, signature)
);
CREATE INDEX IF NOT EXISTS token_transactions_sequence ON token_transactions(sequence);
CREATE INDEX IF NOT EXISTS token_transactions_pending ON token_transactions(mint,status,retry_after,slot,sequence);
CREATE TABLE IF NOT EXISTS token_totals (
  mint TEXT NOT NULL,
  quote_mint TEXT NOT NULL,
  quote_unit TEXT NOT NULL,
  decimals INTEGER NOT NULL,
  creator_fee_raw TEXT NOT NULL,
  trades BIGINT NOT NULL,
  PRIMARY KEY (mint,quote_mint)
);
