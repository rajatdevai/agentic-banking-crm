# Morning digest report generation Celery task — runs at 6 AM daily.
# Generates an RM-specific digest: top 10 priority customers today,
# yesterday's outreach delivery stats, new events detected overnight.
# Pushes to RM dashboard (WebSocket) and optionally sends via email.

from services.workers.celery_app import app


@app.task(name="services.workers.tasks.report_gen.run_report_gen",
          queue="scoring", bind=True, max_retries=3)
def run_report_gen(self):
    """6 AM daily: generate and push the RM morning intelligence digest."""
    # TODO: implement report generation in Phase 8 (notifications layer)
    raise NotImplementedError("report_gen not yet implemented")
