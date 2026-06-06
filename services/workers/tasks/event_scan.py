# Event scan Celery task — runs every 15 minutes.
# Pulls latest transactions from CBS via MCP/API for all active customers.
# Runs the event detection rule engine over new transactions.
# Inserts newly detected events into detected_events table.
# Triggers re-scoring for customers where a new event was detected.
# Pushes real-time WebSocket alert to the RM if event confidence > 0.8.

from services.workers.celery_app import app


@app.task(name="services.workers.tasks.event_scan.run_event_scan",
          queue="events", bind=True, max_retries=5)
def run_event_scan(self):
    """Every 15 minutes: scan for new life event signals in incoming transactions."""
    # TODO: implement event scan pipeline in Phase 5 (orchestrator layer)
    raise NotImplementedError("event_scan not yet implemented")
