# Celery application factory for RM Copilot async workers.
# Broker: Redis (DB 1). Result backend: Redis (DB 2).
# Queue isolation: scoring | events | outreach | embeddings
# Each queue maps to a dedicated worker pool to prevent heavy batch jobs
# (daily_scoring) from starving real-time tasks (outreach_dispatch).

from celery import Celery
import os

app = Celery("rm_copilot")

app.config_from_object({
    "broker_url": os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/1"),
    "result_backend": os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/2"),
    "task_serializer": "json",
    "result_serializer": "json",
    "accept_content": ["json"],
    "timezone": "Asia/Kolkata",
    "enable_utc": True,
    "task_routes": {
        "services.workers.tasks.daily_scoring.*": {"queue": "scoring"},
        "services.workers.tasks.event_scan.*": {"queue": "events"},
        "services.workers.tasks.outreach_dispatch.*": {"queue": "outreach"},
        "services.workers.tasks.embedding_sync.*": {"queue": "embeddings"},
        "services.workers.tasks.report_gen.*": {"queue": "scoring"},
    },
})

# Auto-discover tasks from the tasks package
app.autodiscover_tasks(["services.workers.tasks"])
