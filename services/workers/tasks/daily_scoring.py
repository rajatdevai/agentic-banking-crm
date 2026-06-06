# Nightly batch scoring Celery task.
# Runs at 2 AM daily (scheduled by Celery Beat).
# Scores every active customer in the portfolio: conversion_probability, churn_risk, CLV.
# Writes scored opportunities to the opportunities table.
# Refreshes the Redis priority queue cache for each RM.
# SLA: must complete before 7 AM (RM login time). Alert fires at 6 AM if not done.

from services.workers.celery_app import app


@app.task(name="services.workers.tasks.daily_scoring.run_daily_scoring",
          queue="scoring", bind=True, max_retries=3)
def run_daily_scoring(self):
    """Nightly: score all active customers and refresh the RM priority queues."""
    # TODO: implement batch scoring pipeline in Phase 7 (ML layer)
    raise NotImplementedError("daily_scoring not yet implemented")
