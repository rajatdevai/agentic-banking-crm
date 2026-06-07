"""
Celery Application — RM Copilot async worker infrastructure.

Queues and their responsibilities:
    scoring   — nightly batch scoring, report generation
    events    — transaction scanning every 15 minutes
    outreach  — message dispatch (WhatsApp / SMS / Email)
    embeddings — RAG ingestion when knowledge base is updated

Time limits (soft → SIGTERM; hard → SIGKILL):
    scoring:    4h soft / 4h 48m hard  (batch job, long-running)
    events:     5m soft / 6m hard      (real-time event detection)
    outreach:   30s soft / 36s hard    (provider API call)
    embeddings: 10m soft / 12m hard    (embedding + indexing)

Retry on broker connection error: backoff 1s → 16s, max 5 attempts.
Task result TTL: 24 hours (results read by dashboard within the day).
"""

from __future__ import annotations

from celery import Celery
from celery.signals import worker_ready
from kombu import Queue

import structlog

logger = structlog.get_logger(__name__)


def _get_redis_urls() -> tuple[str, str]:
    """Read broker/backend URLs from settings, fallback to env/defaults."""
    try:
        from shared.config.settings import get_settings
        s = get_settings()
        redis_base = s.REDIS_URL.rstrip("/")
        broker_url = f"{redis_base.rsplit('/', 1)[0]}/1"   # DB 1 for broker
        backend_url = f"{redis_base.rsplit('/', 1)[0]}/2"  # DB 2 for results
        return broker_url, backend_url
    except Exception:
        return "redis://localhost:6379/1", "redis://localhost:6379/2"


_BROKER_URL, _BACKEND_URL = _get_redis_urls()

# Create the Celery application
app = Celery("rm_copilot")

app.conf.update(
    # Broker + backend
    broker_url=_BROKER_URL,
    result_backend=_BACKEND_URL,
    broker_connection_retry_on_startup=True,
    broker_connection_retry=True,
    broker_connection_max_retries=5,

    # Serialisation
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # Timezone
    timezone="Asia/Kolkata",
    enable_utc=True,

    # Result lifecycle
    result_expires=86400,   # 24 hours
    task_ignore_result=False,
    task_store_errors_even_if_ignored=True,

    # Queue definitions (with routing)
    task_queues=[
        Queue("scoring",    routing_key="scoring.#"),
        Queue("events",     routing_key="events.#"),
        Queue("outreach",   routing_key="outreach.#"),
        Queue("embeddings", routing_key="embeddings.#"),
    ],
    task_default_queue="scoring",

    # Route tasks to correct queues
    task_routes={
        "services.workers.tasks.daily_scoring.*":   {"queue": "scoring"},
        "services.workers.tasks.report_gen.*":      {"queue": "scoring"},
        "services.workers.tasks.event_scan.*":      {"queue": "events"},
        "services.workers.tasks.outreach_dispatch.*": {"queue": "outreach"},
        "services.workers.tasks.embedding_sync.*":  {"queue": "embeddings"},
    },

    # Time limits (seconds)
    # Format: {task_path: {"soft": X, "hard": Y}}
    task_soft_time_limit=300,       # Default: 5 minutes
    task_time_limit=360,            # Default: 6 minutes (20% above soft)

    # Per-queue time limits set at worker startup via command line:
    #   celery -A services.workers.celery_app worker -Q scoring
    #       --soft-time-limit=14400 --time-limit=17280
    #   celery -A services.workers.celery_app worker -Q events
    #       --soft-time-limit=300 --time-limit=360
    #   celery -A services.workers.celery_app worker -Q outreach
    #       --soft-time-limit=30 --time-limit=36
    #   celery -A services.workers.celery_app worker -Q embeddings
    #       --soft-time-limit=600 --time-limit=720

    # Acks late: task not acknowledged until it completes (safe retries)
    task_acks_late=True,
    task_reject_on_worker_lost=True,

    # Worker concurrency
    worker_prefetch_multiplier=1,   # One task at a time per worker (fair dispatch)

    # Retry policy for transient failures
    task_publish_retry=True,
    task_publish_retry_policy={
        "max_retries": 5,
        "interval_start": 1,
        "interval_step": 2,
        "interval_max": 16,
    },
)

# Auto-discover all tasks in services/workers/tasks/
app.autodiscover_tasks([
    "services.workers.tasks.daily_scoring",
    "services.workers.tasks.event_scan",
    "services.workers.tasks.outreach_dispatch",
    "services.workers.tasks.embedding_sync",
    "services.workers.tasks.report_gen",
])


@worker_ready.connect
def on_worker_ready(sender, **kwargs):
    logger.info("celery_worker_ready", hostname=sender.hostname)
