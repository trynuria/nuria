-- PostgreSQL maintenance migration; apply after token-transactions.sql.
-- The exported-snapshot backup runs as the engine role. Offline schema users may
-- not define that production role; do not change ownership or writer privileges.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nuria_engine') THEN
    GRANT SELECT ON TABLE public.token_transactions, public.token_totals TO nuria_engine;
  END IF;
END
$$;
