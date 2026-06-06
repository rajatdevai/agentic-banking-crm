# Outreach dispatch Celery task — triggered by new outreach_campaigns insert after RM approval.
# Enforces per-provider rate limits (WhatsApp: 1000/day/number).
# Checks DND registry before sending.
# Calls appropriate notification provider (WhatsApp / SMS / Email).
# Updates outreach_campaigns with sent_at and provider_message_id.
# Retries with exponential backoff on transient provider failures.

from services.workers.celery_app import app


@app.task(name="services.workers.tasks.outreach_dispatch.run_outreach_dispatch",
          queue="outreach", bind=True, max_retries=5)
def run_outreach_dispatch(self, campaign_id: str):
    """Triggered: dispatch an RM-approved outreach message to the customer."""
    # TODO: implement dispatch pipeline in Phase 8 (notifications layer)
    raise NotImplementedError("outreach_dispatch not yet implemented")
