-- ============================
-- PostgreSQL Initialization
-- MT5 Web Quantitative Trading Platform
-- ============================

-- Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- Set timezone to UTC
ALTER DATABASE mt5_quant SET timezone TO 'UTC';

-- Set statement timeout (5 minutes for long-running analytical queries)
ALTER DATABASE mt5_quant SET statement_timeout TO '300000';

-- Set idle-in-transaction session timeout (10 minutes)
ALTER DATABASE mt5_quant SET idle_in_transaction_session_timeout TO '600000';

-- Raise a notice confirming successful initialization
DO $$
BEGIN
    RAISE NOTICE 'Database mt5_quant initialized successfully with TimescaleDB';
END $$;