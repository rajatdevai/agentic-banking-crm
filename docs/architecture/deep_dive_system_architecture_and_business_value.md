# RM Copilot — Enterprise Banking Intelligence Platform
## Deep-Dive System Architecture, Technical Specs & Business Whitepaper

> **Document Version**: 1.0.0  
> **Target Audience**: Chief Technology Officers (CTOs), Head of Retail Banking, Compliance & InfoSec Officers, Engineering Leads  
> **Status**: Production-Ready Design Document

---

## 1. Executive Summary & Business Perspective

### The Problem in Wealth Management & Retail Banking
In modern enterprise banking, Relationship Managers (RMs) are overloaded with data but starved for actionable insights. They manage portfolios of **300 to 1,000 high-net-worth (HNW) or retail customers**, each generating hundreds of transaction logs daily. RMs spend **over 60% of their working hours** manually combing through spreadsheets, transaction logs, and internal CRM systems trying to identify:
1. **Life Events**: When is a customer getting married, buying a home, expanding a business, or sending their child abroad?
2. **Product Propensity**: Which of the bank's dozens of financial products is the customer most likely to purchase *right now*?
3. **Outreach Execution**: Writing personalized emails or WhatsApp messages that adhere strictly to compliance guidelines (DND registry, RBI rules, internal credit limits).

Because of this manual overhead, banks suffer from **high customer churn, low conversion rates on marketing campaigns (typically < 1.5%), and delayed responses to critical customer milestones**, resulting in missed revenue opportunities.

### The RM Copilot Solution
**RM Copilot** is a state-of-the-art, AI-augmented CRM intelligence platform designed to transform relationship banking from a **reactive** model to a **proactive, event-driven** model. 

```
   ┌─────────────────────────────────────────────────────────────┐
   │                  RAW BANKING DATA PIPELINE                  │
   │  (TimescaleDB Transaction Streams + Core Banking SQL DB)    │
   └──────────────────────────────┬──────────────────────────────┘
                                  │
                                  ▼
   ┌─────────────────────────────────────────────────────────────┐
   │            DETERMINISTIC EVENT DETECTION ENGINE             │
   │   (Rule-based trigger: Wedding / House Purchase / Salary)   │
   └──────────────────────────────┬──────────────────────────────┘
                                  │
                                  ▼
   ┌─────────────────────────────────────────────────────────────┐
   │             XGBOOST OPPORTUNITY SCORING ENGINE              │
   │       (Calculates conversion probability using ML)          │
   └──────────────────────────────┬──────────────────────────────┘
                                  │
                                  ▼
   ┌─────────────────────────────────────────────────────────────┐
   │               HYBRID RAG POLICY ENFORCEMENT                 │
   │    (Validates eligibility against RBI & internal rules)      │
   └──────────────────────────────┬──────────────────────────────┘
                                  │
                                  ▼
   ┌─────────────────────────────────────────────────────────────┐
   │              PII-MASKED GENERATIVE OUTREACH                 │
   │  (Generates personalized, compliant outreach suggestions)    │
   └─────────────────────────────────────────────────────────────┘
```

### Business Value Metrics
By automating the detection-to-outreach pipeline, RM Copilot delivers substantial, measurable improvements across key banking metrics:

| Metric | Before RM Copilot | With RM Copilot | Business Impact |
| :--- | :--- | :--- | :--- |
| **Campaign Conversion Rate** | 1.2% – 1.8% | **8.5% – 12.0%** | Over **5x increase** in product sales volume. |
| **Milestone Response Latency** | 3 – 7 Days (or missed entirely) | **Real-Time (< 15 Minutes)** | High-value customers are engaged *immediately* when their purchase intent is highest. |
| **Outreach Draft Generation Time**| 15 – 20 Minutes per customer | **Instant (< 3 Seconds)** | RMs can approve/personalize **50+ campaigns per hour** instead of manually writing 3. |
| **Compliance Infractions** | Occasional manual violations of DND/Credit Limits | **0% Systemic Infractions** | Automatic programmatic policy enforcement via the RAG and DND validation layers. |
| **Customer Wallet Share** | Fragmented across multiple banks | **Substantially Increased** | Proactive product recommendation secures primary bank status. |

---

## 2. End-to-End System Architecture

RM Copilot is built using a modern, loosely coupled, containerized architecture that segregates compute-intensive AI operations, transactional API routing, and high-throughput background processing.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CLIENT LAYER                                    │
│   RM Dashboard (React/Vite) ── Mobile App ── Copilot Chat UI ── Admin        │
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

### Architectural Subsystems

#### 1. API Gateway Layer (FastAPI)
Acts as the single entry point for all client requests.
- **Security**: Validates JSON Web Tokens (JWT) signed by the bank's central IAM.
- **PII Protection**: Integrates Microsoft Presidio to scan incoming RM search queries. Any personal details (phone numbers, email addresses, names, account numbers) are swapped with tokens before passing requests downstream.
- **Rate Limiting**: Utilizes Redis for sliding-window rate limiting to shield downstream AI models and databases from denial-of-service attempts.

#### 2. LangGraph Orchestration Engine
A directed acyclic graph (DAG) that controls execution logic. Instead of chaining LLMs together sequentially, LangGraph maintains a central `AgentState` object. Agents read from this state, perform specialized tasks (deterministic SQL queries, ML inference, or RAG searches), and write their results back. 

#### 3. Hybrid Data Layer (PostgreSQL + TimescaleDB + pgvector)
A unified database instance that serves three distinct workloads:
- **Relational DB**: Standard PostgreSQL tables housing customer demographic info, RM allocations, and communication history.
- **Time-Series DB (TimescaleDB)**: Hypertables optimized for storing millions of historical transaction logs, enabling fast rolling averages and spend pattern calculations.
- **Vector DB (pgvector)**: Stores HNSW indexes of embedded bank policy guidelines, regulatory circulars, and relationship playbooks.

#### 4. Machine Learning & Inference Layer
Dual-model architecture splitting deterministic calculations from language generation:
- **Tabular Models (XGBoost / LightGBM)**: Execute local classification of conversion and churn probabilities. Extremely low latency (<5ms) and 100% reproducible.
- **Generative Models (OpenAI gpt-4o / gpt-4o-mini)**: Leveraged solely for summarizing findings (Reasoning Cards) and drafting outreach emails or WhatsApp messages.

#### 5. Asynchronous Task Worker Layer (Celery + Redis)
Offloads heavy processing to background workers:
- **`daily_scoring`**: Runs at 2:00 AM daily, running ML models across all customer profiles.
- **`event_scan`**: Runs every 15 minutes, searching for transaction triggers.
- **`outreach_dispatch`**: Handles DND checks, contact validation, rate limiters, and Twilio/SendGrid API dispatches.

---

## 3. The Shared Whiteboard Pattern (LangGraph)

The core orchestration design uses a **Shared Whiteboard Pattern** implemented through LangGraph. In typical LLM-agent frameworks, agents talk directly to each other, creating fragile pipelines. If Agent A's output format changes, Agent B breaks.

In RM Copilot, agents communicate exclusively by reading from and writing to a structured state dictionary (`AgentState`).

```
                              ┌────────────────┐
                              │   AgentState   │
                              └───────┬────────┘
                                      │
              ┌───────────────────────┼───────────────────────┐
              │                       │                       │
              ▼                       ▼                       ▼
      ┌──────────────┐        ┌──────────────┐        ┌──────────────┐
      │Customer Intel│        │  Txn Intel   │        │Event Detect. │
      └──────┬───────┘        └──────┬───────┘        └──────┬───────┘
             │                       │                       │
             └───────────────────────┼───────────────────────┘
                                     │ (Writes data)
                                     ▼
                              ┌────────────────┐
                              │   AgentState   │
                              └───────┬────────┘
                                      │
                                      ▼
                              ┌──────────────┐
                              │  Risk Agent  │
                              └──────┬───────┘
                                     │
                                     ▼
                              ┌──────────────┐
                              │Scoring Agent │
                              └──────┬───────┘
                                     │
                                     ▼
                              ┌──────────────┐
                              │ Product Rec  │
                              └──────┬───────┘
                                     │
                                     ▼
                              ┌──────────────┐
                              │Explainability│
                              └──────┬───────┘
                                     │
                                     ▼
                              ┌──────────────┐
                              │ Outreach Gen │
                              └──────┬───────┘
                                     │
                                     ▼
                              ┌──────────────┐
                              │  RM Copilot  │
                              └──────────────┘
```

### AgentState Structure
```python
class AgentState(TypedDict):
    customer_ids: List[UUID]
    rm_id: UUID
    target_product: Optional[str]
    
    # Extracted profiles and summaries
    customer_profiles: Dict[UUID, dict]       # Written by Customer Intel
    transaction_summaries: Dict[UUID, dict]   # Written by Transaction Intel
    detected_events: Dict[UUID, List[dict]]    # Written by Event Detection
    risk_flags: Dict[UUID, List[str]]          # Written by Risk Assessment
    
    # ML Scoring
    conversion_probabilities: Dict[UUID, float] # Written by Opportunity Scoring
    churn_probabilities: Dict[UUID, float]       # Written by Opportunity Scoring
    
    # RAG & NLG
    product_recommendations: Dict[UUID, dict]  # Written by Product Rec
    reasoning_cards: Dict[UUID, str]           # Written by Explainability
    outreach_drafts: Dict[UUID, dict]          # Written by Outreach Gen
```

### Execution Step Sequence
1. **Parallel Fan-out (Steps 5a, 5b, 5c)**: The graph executes `Customer Intel`, `Transaction Intel`, and `Event Detection` simultaneously. They pull raw SQL tables, compile rolling 3-month transaction sums, and check rules, writing their payloads into `customer_profiles`, `transaction_summaries`, and `detected_events` keys in parallel.
2. **Risk Check (Step 6)**: The `Risk Assessment` agent reads the compiled customer profiles. It verifies the CIBIL score and debt burden. If CIBIL is less than 650 or FOIR exceeds 50%, it tags the state with `risk_flags`.
3. **ML Scoring (Step 7)**: The `Opportunity Scoring` agent reads the transaction summary and profile, converts them into a feature vector, and passes them to the XGBoost/LightGBM model. The resulting conversion score is written to the state.
4. **Product Recommendation RAG (Step 8)**: The `Product Rec` agent checks the product eligibility policies via a vector database search, ensuring the product matches the customer's risk profile.
5. **NLG Generation (Steps 9 & 10)**: The `Explainability` agent uses `gpt-4o` to generate natural-language reasons for the recommendation. The `Outreach Gen` agent retrieves playbooks via RAG and writes personalized messages.
6. **Delivery (Step 11)**: The final assembled state is formatted and returned to the client dashboard.

---

## 4. Machine Learning & Heuristics Calibration

### Tabular ML vs. LLM Reasoning
A common anti-pattern in modern AI applications is using LLMs to score or categorize structured data (e.g. "GPT, look at these 50 transactions and give a score from 1-10"). This approach is slow, expensive, prone to hallucination, and violates compliance requirements for explainable credit scoring.

RM Copilot enforces a strict division of labor:

```
                  ┌─────────────────────────────────────┐
                  │          Incoming Request           │
                  └──────────────────┬──────────────────┘
                                     │
                  ┌──────────────────┴──────────────────┐
                  │      Is the task quantitative?      │
                  └──────┬──────────────────────┬───────┘
                         │ Yes                  │ No
                         ▼                      ▼
            ┌────────────────────────┐    ┌───────────┐
            │   Tabular Models /     │    │    LLM    │
            │  Deterministic Rules   │    │  (GPT-4)  │
            │  (XGBoost / SQL / C#)  │    └───────────┘
            └────────────────────────┘
```

#### Why XGBoost?
- **Speed**: Inference takes under **2 milliseconds**, compared to 2-5 seconds for an LLM API call.
- **Auditability / Explainability**: Feature importance can be extracted (using SHAP values or built-in gain values). If a customer asks, "Why did you offer me this loan rate?", the bank can point directly to specific features (e.g., CIBIL score, salary increase, low debt ratio).
- **Deterministic**: The same input always yields the exact same conversion probability score.

### Feature Engineering Pipeline
The XGBoost model processes the following features compiled by the `Transaction Intel` and `Customer Intel` agents:
- `cibil_score`: Credit score from the credit bureau.
- `avg_3m_debits`: Average monthly debit transactions over the last 3 months.
- `salary_growth_rate`: Growth rate of salary transactions month-over-month.
- `spend_to_income_ratio`: Monthly debits divided by monthly credit transactions.
- `active_products_count`: Number of active loan or deposit accounts the customer currently holds.
- `days_since_last_interaction`: Recency of outreach.

---

## 5. Advanced RAG Retrieval Pipeline

To ensure the LLM drafts messages and reasoning cards that comply with bank policy, the system runs an advanced **two-stage hybrid retrieval RAG pipeline**.

```
Knowledge Document (PDF, MD, CSV)
        │
        ▼
[Semantic Chunker] (Splits on headers/paragraph boundaries, 512-1024 tokens)
        │
        ▼
[Embedder] (text-embedding-3-large, 1536 dimensions)
        │
        ▼
[pgvector Index] (HNSW index, Cosine distance)

==================== RETRIEVAL TIME ====================

Incoming Search Query
        │
        ├─────────────────────────────────────────┐
        ▼ (Dense Search)                          ▼ (Sparse Search)
Vector Cosine Match                       BM25 Full Text Search
  (Finds top 20 chunks)                    (Finds top 20 chunks)
        │                                         │
        └────────────────────┬────────────────────┘
                             │
                             ▼
                 Reciprocal Rank Fusion (RRF)
                     (Top 30 candidates)
                             │
                             ▼
                 Cross-Encoder Reranker
                 (gpt-4o-mini / Cohere)
                     (Top 5 chunks)
                             │
                             ▼
                    Context Constructor
                 (Injected into LLM Prompt)
```

### Step-by-Step Breakdown

1. **Semantic Chunking**: Instead of arbitrarily splitting documents every 500 characters (which breaks up bulleted tables or policy rules), the loader chunks documents based on structural markdown headers and paragraph boundaries.
2. **Dense Vector Search**: The system converts the query into a 1536-dimension vector and executes a cosine similarity search against the `knowledge_embeddings` table using an HNSW (Hierarchical Navigable Small World) index for fast lookup.
3. **Sparse Keyword Search**: Simultaneously, the system runs a BM25 full-text keyword search. Dense search is excellent for capturing conceptual matches (e.g., "startup funding" matching "venture capital"), while sparse search is superior for exact terms (e.g., specific regulation codes like "Section 42A").
4. **Reciprocal Rank Fusion (RRF)**: Merges the dense and sparse result sets, scoring chunks based on their positions in both lists.
5. **Cross-Encoder Reranking**: The top 30 merged candidates are evaluated by a deep cross-encoder model. The cross-encoder analyzes the query and the chunk text together, calculating a highly accurate relevance score. The top 5 ranked chunks are then sent to the LLM.

---

## 6. Security, Compliance & Resiliency

Operating in a banking environment requires stringent security measures. RM Copilot integrates safety protocols throughout the pipeline.

### 1. PII Masking with Microsoft Presidio
Before customer details leave the bank's secure network and go to external LLM APIs (like OpenAI), the system runs a PII scrubbing process:
- All queries and profiles are evaluated using regex and Named Entity Recognition (NER) models.
- Fields like Names, Email addresses, Credit Card numbers, Phone numbers, and Bank Account numbers are replaced with placeholders (e.g., `[PERSON_1]`, `[PHONE_1]`).
- A temporary token map is written to Redis with a short time-to-live (TTL) equal to the session duration.
- When the LLM returns the generated message, the gateway swaps the placeholders back with the original details before returning the response to the user.

```
Incoming Customer Data ──► [Presidio NER Scanner] ──► PII replaced with [PERSON_1] ──► Sent to OpenAI API
                                                                                            │
                                                                                            ▼
RM Dashboard ◄── [Replaced with real values] ◄── Outbound Response ◄── Received from OpenAI
```

### 2. Core Banking Service (CBS) Circuit Breaker
If the external Core Banking System API experiences downtime, a circuit breaker (modeled in `cbs_tools.py`) trips:
- **Normal (Closed)**: Queries go to the CBS database.
- **Tripped (Open)**: If 5 failures occur in 10 seconds, the breaker opens, bypassing the CBS and returning cached or default heuristic profiles for 60 seconds to prevent resource exhaustion.
- **Recovery (Half-Open)**: After 60 seconds, a test query is sent. If it succeeds, the connection is restored. If it fails, the cool-off timer resets.

### 3. Human-in-the-Loop (HITL) Gate
To prevent hallucinated or inappropriate messages from reaching customers, the system does not allow direct automated outreach.
- The `Outreach Gen` agent generates a *draft* message.
- The message is held in a "Pending Approval" state on the RM's queue.
- The RM can edit, rewrite, or reject the draft.
- The message is only queued for dispatch once the RM clicks the "Approve" button.

---

## 7. Future Scalability Plan

To transition RM Copilot from a single-region deployment to a global bank infrastructure supporting tens of thousands of active users, we recommend the following scale-out roadmap.

```
                       ┌────────────────────────────────┐
                       │    Core Banking System (CBS)   │
                       └───────────────┬────────────────┘
                                       │ (Kafka Transaction Log Stream)
                                       ▼
                       ┌────────────────────────────────┐
                       │      Apache Kafka Broker       │
                       └──────┬──────────────────┬──────┘
                              │                  │
                              ▼                  ▼
                       ┌──────────────┐   ┌──────────────┐
                       │Consumer App 1│   │Consumer App 2│
                       └──────┬───────┘   └──────┬───────┘
                              │                  │ (Writes)
                              ▼                  ▼
                       ┌──────────────┐   ┌──────────────┐
                       │ TimescaleDB  │   │  Qdrant /    │
                       │  Hypertable  │   │   Milvus     │
                       └──────────────┘   └──────────────┘
```

### Phase 1: High-Throughput Event Streaming (Celery to Kafka)
- **Current Limitation**: Celery utilizes polling or light Redis message queues, which can bottleneck when processing millions of transactions per second.
- **Migration Plan**: Integrate Apache Kafka. Configure Kafka connectors to stream the bank's core ledger updates directly into partition topics. Write lightweight consumer services in Go or Rust to ingest transactions and stream them straight to TimescaleDB hypertables.

### Phase 2: Vector Database Scaling (pgvector to Qdrant)
- **Current Limitation**: Storing vector embeddings inside the primary PostgreSQL database simplifies initial operations but can lead to resource contention as the database scales.
- **Migration Plan**: Migrate embeddings to a dedicated vector database cluster like Qdrant or Milvus. These platforms support distributed search, memory-efficient index quantization (Scalar Quantization), and horizontal scaling of vector queries independent of the primary SQL database.

### Phase 3: Distributed Multi-Agent Architectures
- **Current Limitation**: Running the entire LangGraph orchestration flow in a single API container limits scaling options for complex, multi-step queries.
- **Migration Plan**: Deconstruct agents into independent microservices communicating via gRPC. Deploy agents on Kubernetes with horizontal pod autoscaling (HPA) to scale agents independently based on workload (e.g., running 100 instances of the outreach generator and only 10 risk checkers).

### Phase 4: Automated Model Training & Monitoring (MLOps)
- **Current Limitation**: The XGBoost model is statically loaded from a serialized file, which can lead to model drift as customer spending patterns evolve.
- **Migration Plan**: Set up an automated MLOps pipeline using Kubeflow or MLflow. Build background tasks to pull historical outreach logs, identify successful conversions, rebuild training sets, retrain the XGBoost model monthly, and deploy the new version using canary rollouts.

---

## 8. Conclusion

RM Copilot demonstrates how modern banks can utilize Agentic AI, machine learning, and semantic search to improve relationship management. By enforcing a **deterministic division of labor**—using machine learning for scoring and rule engines for event detection, while reserving LLMs strictly for natural language generation—the platform delivers a secure, auditable, and highly scalable solution. 

Implementing the architecture, RAG pipelines, and security layers detailed in this document provides banks with a reliable framework to increase conversion rates, reduce operational overhead, and deliver personalized customer service at scale.
