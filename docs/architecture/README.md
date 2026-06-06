# Architecture Documentation

This directory contains detailed architecture documentation for the RM Copilot platform.

## Contents

- `README.md` — This file. Overview and links to detailed docs.
- See the root `README.md` for the complete architecture diagram and execution flow.

## Architecture Decision Records (ADRs)

ADRs documenting key design decisions are planned for this directory:

| ADR | Decision | Status |
|-----|----------|--------|
| ADR-001 | Use LangGraph for agent orchestration (vs. custom state machine) | Accepted |
| ADR-002 | Deterministic rule engine for event detection (vs. LLM) | Accepted |
| ADR-003 | XGBoost for conversion scoring (vs. LLM reasoning) | Accepted |
| ADR-004 | pgvector over Qdrant for initial deployment | Accepted |
| ADR-005 | PII masking via Presidio before all LLM calls | Accepted |
| ADR-006 | RM approval gate before outreach dispatch | Accepted |
| ADR-007 | Celery over Kafka for async workers at current scale | Accepted |
| ADR-008 | Single OpenAI provider (gpt-4o / gpt-4o-mini) | Accepted |
