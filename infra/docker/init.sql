-- PostgreSQL initialization script for RM Copilot
-- Run automatically by Docker on first start
-- Enables required extensions: pgvector, TimescaleDB, uuid-ossp

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
