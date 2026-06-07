"""
Celery Beat Schedule — periodic task definitions.

All times are defined in UTC (Celery uses UTC internally).
IST = UTC + 5:30, so:
    2:00 AM IST  = 20:30 UTC (previous day)
    6:00 AM IST  = 00:30 UTC

Tasks:
    daily_scoring   — 2:00 AM IST (20:30 UTC)
                      Re-scores all active customers, builds RM priority queues
    event_scan      — Every 15 minutes
                      Scans new transactions for life events
    report_gen      — 6:00 AM IST (00:30 UTC)
                      Builds morning digest for each RM before they log in
"""

from celery.schedules import crontab

from services.workers.celery_app import app

app.conf.beat_schedule = {
    # -----------------------------------------------------------------------
    # Nightly customer scoring — 2:00 AM IST = 20:30 UTC
    # -----------------------------------------------------------------------
    "daily_scoring": {
        "task": "services.workers.tasks.daily_scoring.run_daily_scoring",
        "schedule": crontab(hour=20, minute=30),   # 20:30 UTC = 02:00 IST
        "options": {"queue": "scoring"},
    },

    # -----------------------------------------------------------------------
    # Transaction event scan — every 15 minutes
    # -----------------------------------------------------------------------
    "event_scan_15min": {
        "task": "services.workers.tasks.event_scan.run_event_scan",
        "schedule": crontab(minute="*/15"),        # Every 15 minutes
        "options": {"queue": "events"},
    },

    # -----------------------------------------------------------------------
    # Morning RM digest — 6:00 AM IST = 00:30 UTC
    # -----------------------------------------------------------------------
    "morning_report_gen": {
        "task": "services.workers.tasks.report_gen.generate_morning_reports",
        "schedule": crontab(hour=0, minute=30),    # 00:30 UTC = 06:00 IST
        "options": {"queue": "scoring"},
    },
}

app.conf.beat_scheduler = "celery.beat:PersistentScheduler"
app.conf.beat_schedule_filename = ".celerybeat-schedule"
