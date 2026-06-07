# RM Copilot — Enterprise Banking Intelligence Platform

An AI-powered Relationship Manager Copilot that analyzes customer transaction data, detects life events, scores customers by conversion probability, recommends banking products, and generates personalized outreach messages.

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CLIENT LAYER                                    │
│   RM Dashboard (React)    Mobile App    Copilot Chat UI    Admin Console     │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │ HTTPS / SSE / WebSocket
┌───────────────────────────────────▼─────────────────────────────────────────┐
│                              GATEWAY LAYER                                   │
│   FastAPI Gateway  │  PII Masking (Presidio)  │  JWT Auth  │  Audit Logger  │
│   Rate Limiter (Redis sliding window)          │  /health /docs /metrics     │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │ Internal HTTP
┌───────────────────────────────────▼─────────────────────────────────────────┐
│                      LANGGRAPH ORCHESTRATION ENGINE                          │
│                                                                              │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │  AgentState (TypedDict) — shared whiteboard, Redis-checkpointed     │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│   [Customer Intel] ──┬── [Transaction Intel] ──┐                            │
│                      └── [Event Detection]  ───┤                            │
│                          [Risk Assessment]  ───┘                            │
│                                  │                                           │
│                      [Opportunity Scoring] (XGBoost)                        │
│                                  │                                           │
│                      [Product Rec] ←── RAG (pgvector)                      │
│                                  │                                           │
│                      [Explainability] (gpt-4o)                              │
│                                  │                                           │
│                      [Outreach Gen] (gpt-4o + RAG)                         │
│                                  │                                           │
│                      [RM Copilot] — streaming response                      │
└───────────────────────────────────┬─────────────────────────────────────────┘
              ┌────────────────────┬┴──────────────────────┐
              │                    │                        │
┌─────────────▼──────┐  ┌─────────▼──────────┐  ┌────────▼────────────────┐
│   DATA LAYER        │  │   AI LAYER          │  │  ASYNC WORKERS (Celery) │
│                     │  │                     │  │                         │
│ PostgreSQL          │  │ OpenAI gpt-4o       │  │ daily_scoring (2 AM)    │
│ TimescaleDB         │  │ OpenAI gpt-4o-mini  │  │ event_scan (15 min)     │
│ pgvector            │  │ text-embedding-3-lg │  │ embedding_sync          │
│ Redis               │  │ XGBoost (conversion)│  │ outreach_dispatch       │
│                     │  │ LightGBM (churn)    │  │ report_gen (6 AM)       │
└─────────────────────┘  └─────────────────────┘  └─────────────────────────┘
              │
┌─────────────▼──────────────────────────────────┐
│              NOTIFICATIONS LAYER                 │
│  WhatsApp (Meta)  │  SMS (Twilio)  │  Email (SG) │
│  DND Check  │  Rate Limit  │  Delivery Tracking  │
└─────────────────────────────────────────────────┘
```

---

## Execution Flow

The primary use case: *"Find high-value customers likely to convert for a personal loan this month and generate personalized WhatsApp messages."*

```
1. RM types query in Copilot UI
        ↓
2. Gateway: JWT validated, session token created
        ↓
3. PII Masking: Presidio scans query, replaces any PII with [TOKEN] placeholders
        ↓
4. Orchestrator: Initialises AgentState {customer_ids, rm_id, target_product: "personal_loan"}
        ↓
5. PARALLEL EXECUTION (nodes 5a, 5b, 5c run simultaneously):
   5a. Customer Intel Agent    → reads DB, writes customer_profiles to state
   5b. Transaction Intel Agent → queries TimescaleDB, writes transaction_summary
   5c. Event Detection Agent   → runs rule engine, writes detected_events
        ↓
6. Risk Assessment Agent → reads credit data, sets risk_flags per customer
        ↓
7. Opportunity Scoring Agent → calls XGBoost model server, writes ranked opportunities
        ↓
8. Product Recommendation Agent → queries RAG (product eligibility rules), validates eligibility
        ↓
9. Explainability Agent (gpt-4o) → generates RM-readable reasoning cards
        ↓
10. Outreach Generation Agent (gpt-4o) → retrieves persona playbook via RAG, drafts messages
        ↓
11. RM Copilot Agent → assembles final response, streams to dashboard via SSE
        ↓
12. RM reviews priority queue + message previews, approves outreach
        ↓
13. Celery task: compliance check → DND check → dispatch via WhatsApp Business API
        ↓
14. Delivery receipts tracked → outreach_campaigns funnel updated
```

---

## Agent Design

| Agent | LLM? | Purpose |
|-------|-------|---------|
| Customer Intel | ❌ | DB reads for customer profile, persona, holdings |
| Transaction Intel | ❌ | TimescaleDB aggregation — spend patterns, behavioral tags |
| Event Detection | ❌ | Deterministic rule engine — MCC pattern → life event |
| Risk Assessment | ❌ | Credit policy rules — CIBIL, FOIR, delinquency flags |
| Opportunity Scoring | ❌ | XGBoost conversion_prob + composite priority score |
| Product Recommendation | ❌+RAG | RAG eligibility lookup + rule-based validation |
| Explainability | ✅ gpt-4o | Plain-English reasoning cards for RM dashboard |
| Outreach Generation | ✅ gpt-4o | Persona-aware message drafting via RAG + LLM |
| RM Copilot | ✅ gpt-4o | Conversational Q&A, streaming, session memory |

**Key design principle**: LLMs are used *only* for natural language generation tasks. All scoring, classification, and rule enforcement is deterministic — fully auditable and reproducible.

---

## Tool Design and Usage

| Tool | Used By | What It Does |
|------|---------|--------------|
| `db_tools.py` | Customer Intel, Opportunity Scoring | Async SQLAlchemy queries to PostgreSQL |
| `vector_tools.py` | Product Rec, Outreach Gen, RM Copilot | Hybrid RAG retrieval (dense + BM25 + rerank) |
| `scoring_tools.py` | Opportunity Scoring | HTTP calls to internal ML model server |
| `cbs_tools.py` | Transaction Intel | CBS API integration with circuit breaker |
| `mcp_tools.py` | Opportunity Scoring, Outreach Gen | Market data (repo rate, indices) with Redis cache |

---

## RAG Pipeline

```
Knowledge Document (MD/PDF/CSV)
        ↓
[Loader] → raw text + metadata
        ↓
[Semantic Chunker] → 512–1024 token chunks, paragraph-boundary aware
        ↓
[Embedder] → text-embedding-3-large, MRL-truncated to 1536 dims
        ↓
[Indexer] → pgvector (HNSW index), content-hash dedup

Retrieval per query:
  Dense (pgvector cosine, top-20) + Sparse (BM25, top-20)
        ↓ Reciprocal Rank Fusion
  top-30 candidates → Cross-encoder reranker → top-5 chunks
        ↓
  Context Builder → assembled context window for LLM prompt
```

**Knowledge base contents**: product eligibility rules, RBI lending guidelines, internal credit policy, persona engagement playbooks, and live market context (repo rate, indices).

---

## Key Design Decisions

### 1. Deterministic Event Detection
Life events are detected using a rule engine (SQL + Python MCCs), not an LLM. This is a deliberate choice: every detection is fully auditable ("rule 7 fired: jewellery MCC + banquet MCC within 60 days"), has zero hallucination risk, runs in milliseconds, and is 100% explainable to compliance teams.

### 2. XGBoost for Conversion Scoring
The conversion probability model uses XGBoost trained on historical campaign outcomes — not LLM reasoning. This gives: native feature importance (auditable), sub-millisecond inference at batch scale, deterministic output (same input = same score), and calibrated probabilities. An LLM cannot reliably provide any of these guarantees.

### 3. PII Never Touches LLM APIs
All customer data is masked by Presidio before any LLM call. Names become `[PERSON_1]`, phone numbers become `[PHONE_1]`, etc. The token-to-PII mapping lives in Redis with a session-scoped TTL. LLM output is de-masked before returning to the RM. This is non-negotiable for banking regulatory compliance.

### 4. RM Approval Is Always Required
The outreach generation pipeline produces *previews*, not sent messages. The RM must explicitly approve each message before dispatch. This ensures the RM owns the customer relationship and every communication.

### 5. AgentState as Shared Whiteboard
No agent calls another agent directly. All agents read from and write to a shared `AgentState` TypedDict, checkpointed to Redis at each step. This enables: parallel fan-out, fault-tolerant resumption on failure, clean separation of concerns, and easy extensibility (add an agent without touching others).

---

## Trade-offs and Limitations

| Decision | Trade-off | Why |
|----------|-----------|-----|
| pgvector over Qdrant | Lower vector query performance at very large scale | Operational simplicity; same Postgres cluster; clear upgrade path |
| Celery over Kafka | Lower throughput ceiling | Right fit for current scale; Kafka adds ops complexity without benefit yet |
| LLM only for NLG | Less flexible agent reasoning | Auditability and cost control outweigh flexibility for financial use case |
| RM approval gate | Slower outreach | Risk management; RM must own every customer communication |
| Single OpenAI provider | No LLM redundancy | Simplicity for demo; add fallback chain in production |
| Heuristic scoring fallback | Less accurate during model downtime | Availability over accuracy — better to show a score than nothing |

**Known limitations for the demo build**:
- XGBoost model is trained on synthetic data (no real historical conversions)
- CBS integration is mocked — real integration requires bank API credentials
- WhatsApp Business API requires Meta approval for the phone number
- No real-time WebSocket push implemented (SSE only for copilot streaming)

---

## Demo Scenarios

### Demo 1 — Priority Queue
RM opens dashboard. System shows ranked list of customers with conversion probability and reasoning. RM asks: *"Show me HNI customers with wealth migration signals."* Copilot filters and explains.

### Demo 2 — Event-Driven Outreach
System detects wedding signals for 3 customers. Morning digest shows them ranked at 87%, 74%, 62% conversion probability. RM reviews generated WhatsApp messages, edits one, approves all. Dispatch queued.

### Demo 3 — Conversational Copilot
RM asks: *"Rahul hasn't responded to two messages, what should I try next?"* Copilot retrieves Rahul's profile, outreach history, and persona playbook via RAG. Suggests email with different angle. RM approves new message.

---

## Setup and Run Instructions

### Prerequisites
- Docker and Docker Compose
- Python 3.11+
- Poetry (`pip install poetry`)
- OpenAI API key

### 1. Clone and configure environment
```bash
git clone <repo-url>
cd rm-copilot
cp .env.example .env
# Edit .env — set OPENAI_API_KEY and other required values
```

### 2. Start infrastructure
```bash
cd infra/docker
docker-compose up postgres redis -d
# Wait for healthchecks to pass
```

### 3. Install dependencies
```bash
poetry install
```

### 4. Run database migrations
```bash
poetry run alembic upgrade head
```

### 5. Seed synthetic data
```bash
poetry run python scripts/seed_db.py
```

### 6. Backfill RAG knowledge base
```bash
poetry run python scripts/backfill_embeddings.py
```

### 7. Start the gateway
```bash
poetry run uvicorn services.gateway.main:app --reload --port 8000
```

### 8. Start Celery workers (separate terminal)
```bash
poetry run celery -A services.workers.celery_app worker --loglevel=info
```

### 9. Start frontend dashboard (separate terminal)
```bash
cd frontend
npm install
npm run dev
```
Open http://localhost:5173 in your browser.
Log in with demo relationship manager credentials:
- Email: `priya@bank.com`
- Password: `password123`

### API Documentation
After starting the gateway, visit:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- Health: http://localhost:8000/health

### Run tests
```bash
poetry run pytest tests/ -v --cov=services --cov=shared
```

---

## Repository Structure

See the full directory tree in `docs/architecture/README.md`.

---

*Built for enterprise banking — production-quality architecture, not a prototype.*
"# agentic-banking-crm" 
