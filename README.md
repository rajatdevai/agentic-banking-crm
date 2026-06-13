# RM Copilot — Enterprise Banking Intelligence Platform

An AI-powered Relationship Manager Copilot that analyzes customer transaction data, detects life events, scores customers by conversion probability, recommends banking products, and generates personalized outreach messages — delivered via WhatsApp (Twilio), SMS, and Email with full delivery tracking.

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
└─────────────────────────────────────────────────────────────────────┘   │
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

The primary usecase: *"Find high-value customers likely to convert for a personal loan this month and generate personalized WhatsApp messages."*

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
| Transaction Intel | ❌ | Spend pattern analysis, merchant category classifications, and behavioral tags |
| Event Detection | ❌ | Deterministic rule engine matching transaction sequences to life events |
| Risk Assessment | ❌ | Credit policy validation (CIBIL score check, FOIR, delinquency flags) |
| Opportunity Scoring | ❌ | XGBoost conversion probability and priority scoring calculation |
| Product Recommendation | ❌+RAG | Product eligibility verification against RAG knowledge base |
| Explainability | ✅ gpt-4o | Plain-English reasoning summary cards for the RM dashboard |
| Outreach Generation | ✅ gpt-4o | Personalized message template drafts based on RAG persona playbooks |
| RM Copilot | ✅ gpt-4o | Interactive dashboard Q&A chatbot with streaming capability and memory |

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

> **Note**: Outreach messages are generated with customer and RM names injected directly into the prompt context (not PII-masked), since personalized greetings ("Dear Neha Gupta") are a core business requirement for outreach communications.

### 4. RM Approval Is Always Required
The outreach generation pipeline produces *previews*, not sent messages. The RM must explicitly approve each message before dispatch. This ensures the RM owns the customer relationship and every communication.

### 5. AgentState as Shared Whiteboard
No agent calls another agent directly. All agents read from and write to a shared `AgentState` TypedDict, checkpointed to Redis at each step. This enables: parallel fan-out, fault-tolerant resumption on failure, clean separation of concerns, and easy extensibility (add an agent without touching others).

---

## Setup and Run Instructions

### Prerequisites
- Python 3.11+
- Poetry (`pip install poetry`)
- Redis (standard service or executable)
- PostgreSQL (or Neon serverless instance)
- OpenAI API key

### 1. Clone and configure environment
```bash
git clone <repo-url>
cd rm-copilot
cp .env.example .env
# Edit .env — set DATABASE_URL, REDIS_URL, OPENAI_API_KEY, and Twilio credentials
```

### 2. Install dependencies
```bash
poetry install
```

### 3. Run database migrations
```bash
poetry run alembic upgrade head
```

### 4. Seed synthetic data
```bash
poetry run python scripts/seed_db.py
```

### 5. Backfill RAG knowledge base
```bash
poetry run python scripts/backfill_embeddings.py
```

---

## Running Locally

### Option A — Automating All Services on Windows (No Docker)
You can run the entire platform locally using the provided PowerShell scripts.

To start the whole project (Database and Redis must be running):
```powershell
# Set ExecutionPolicy if needed, then run the startup script
powershell -ExecutionPolicy Bypass -File .\start_local.ps1
```
This script validates your setup, verifies Redis connectivity, and launches 4 separate terminal windows running:
1. **FastAPI Gateway** (`http://localhost:8000`)
2. **Celery Worker** (`outreach`, `scoring`, `events`, `embeddings` queues)
3. **Celery Beat** (Periodic tasks scheduler)
4. **Vite Frontend Dev Server** (`http://localhost:5173`)

To stop all background services and close their respective terminal windows:
```powershell
powershell -ExecutionPolicy Bypass -File .\stop_local.ps1
```

### Option B — Run Services Manually

1. **Start the gateway**:
   ```bash
   poetry run uvicorn services.gateway.main:app --reload --port 8000
   ```
2. **Start Celery workers** (Windows must use `--pool=solo`):
   ```bash
   poetry run celery -A services.workers.celery_app worker --loglevel=info -Q outreach,scoring,events,embeddings --pool=solo
   ```
3. **Start Celery Beat**:
   ```bash
   poetry run celery -A services.workers.celery_app beat --loglevel=info --scheduler celery.beat:PersistentScheduler
   ```
4. **Start Vite frontend**:
   ```bash
   cd frontend
   npm run dev
   ```

Open `http://localhost:5173` in your browser.
Log in with demo credentials:
* **RM 1**: Email: `arjun@bank.com` / Password: `password123`
* **RM 2**: Email: `priya@bank.com` / Password: `password123`

---

## Performance Optimization & Connection Re-use

To reduce Queue & Dispatch latency (minimizing time gaps when an RM clicks **Approve & Dispatch** in the UI), we optimized the connection lifecycle of our workers:
* **Connection Re-use**: Celery worker tasks now import and reuse `AsyncSessionLocal` from [session.py](file:///c:/Users/rajat/Desktop/crm-agent/shared/db/session.py) instead of spinning up a new SQLAlchemy database engine (`create_async_engine`) and disposing of it on every single task run.
* **Handshake Elimination**: This eliminates the expensive TCP and SSL handshake latency overhead (especially noticeable on serverless/cloud databases like Neon), reducing database session start times in workers to near `0ms`.

---

## Testing & Developer Utilities

To make local testing and campaign dispatches easy, we included several developer scripts under the `scratch/` and `scripts/` directories:

### 1. Update Test Phone Numbers
To route all outbound SMS and WhatsApp messages to your own mobile device instead of dummy database records, run the phone update utility:
```bash
# Modifies the 'phone' column for all customer rows in the DB
poetry run python scratch/update_phones.py
```

### 2. Direct Campaign Dispatch
To test the Celery unmasking and Twilio API payload generation locally and synchronously without queuing:
```bash
poetry run python scratch/test_dispatch.py
```
This utility grabs the latest campaign draft, unmasks the recipient's phone number, triggers the compliance rules, runs the dispatch, and prints the Twilio message SID.

### 3. Batch Dispatching Pending Campaigns
To batch dispatch all unsent campaign drafts in the database with optional contact overrides:
```bash
# Dispatch all pending email/whatsapp/sms drafts
poetry run python scripts/dispatch_pending.py

# Dispatch all drafts, redirecting them to your personal test credentials
poetry run python scripts/dispatch_pending.py --phone +919999999999 --email you@example.com
```

### 4. Running the Test Suite
```bash
poetry run pytest tests/ -v
```

---

## WhatsApp Sandbox Setup (Twilio)
To receive dispatched WhatsApp messages on your phone during demo:

1. Go to [Twilio Console → Messaging → Try it out → Send a WhatsApp message](https://console.twilio.com/us1/develop/sms/try-it-out/whatsapp-learn)
2. Note your sandbox code (e.g., `join fix-community`)
3. From your phone, send that message to **+1 415 523 8886** on WhatsApp.
4. Once joined, update `.env` with your Twilio credentials:
   ```env
   TWILIO_ACCOUNT_SID=your_account_sid
   TWILIO_AUTH_TOKEN=your_auth_token
   WHATSAPP_PHONE_NUMBER_ID=+14155238886
   ```
5. Ensure your test phone number is updated in the database (or run the `scratch/update_phones.py` script).
6. Generate and approve an outreach → the message will arrive on your WhatsApp device.

---

*Built for enterprise banking — production-quality architecture, not a prototype.*
