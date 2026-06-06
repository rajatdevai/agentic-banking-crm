# Celery Beat schedule — defines all recurring cron jobs for the RM Copilot platform.
# Schedules: daily_scoring (2 AM), event_scan (every 15 min),
# report_gen (6 AM), and any other time-based tasks.
# This file is the single source of truth for all scheduled work.

from celery.schedules import crontab
from services.workers.celery_app import app

app.conf.beat_schedule = {
    # Nightly: score all active customers — must complete before 7 AM RM login
    "daily-customer-scoring": {
        "task": "services.workers.tasks.daily_scoring.run_daily_scoring",
        "schedule": crontab(hour=2, minute=0),
        "options": {"queue": "scoring"},
    },
    # Every 15 minutes: scan new transactions for life event signals
    "event-scan": {
        "task": "services.workers.tasks.event_scan.run_event_scan",
        "schedule": crontab(minute="*/15"),
        "options": {"queue": "events"},
    },
    # Every morning: generate RM digest with top opportunities
    "morning-report-gen": {
        "task": "services.workers.tasks.report_gen.run_report_gen",
        "schedule": crontab(hour=6, minute=0),
        "options": {"queue": "scoring"},
    },
}
