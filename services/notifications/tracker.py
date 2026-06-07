# Outreach delivery tracker.
# Updates outreach_campaigns table with delivery receipts received via provider webhooks.
# Tracks the full funnel: sent → delivered → opened → converted.

from __future__ import annotations

from datetime import datetime, timezone
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.models import OutreachCampaign

logger = structlog.get_logger(__name__)


async def process_delivery_receipt(payload: dict, db: AsyncSession) -> bool:
    """
    Parses webhook payload from WhatsApp, Twilio, or SendGrid,
    locates the campaign by provider_message_id, and updates its tracking timestamps.
    """
    logger.info("webhook_received", payload=payload)

    provider_id = None
    status = None

    # 1. Parse WhatsApp payload
    if "entry" in payload:
        try:
            changes = payload["entry"][0]["changes"][0]["value"]
            if "statuses" in changes:
                status_obj = changes["statuses"][0]
                provider_id = status_obj.get("id")
                status = status_obj.get("status")  # "sent", "delivered", "read", "failed"
        except (IndexError, KeyError, TypeError):
            pass

    # 2. Parse Twilio payload
    elif "MessageSid" in payload:
        provider_id = payload.get("MessageSid")
        status = payload.get("MessageStatus")  # "queued", "sending", "sent", "delivered", "undelivered", "failed"

    # 3. Parse SendGrid payload (SendGrid sends an array of events)
    elif isinstance(payload, list) and len(payload) > 0 and "sg_message_id" in payload[0]:
        event_obj = payload[0]
        raw_id = event_obj.get("sg_message_id")
        provider_id = raw_id.split(".")[0] if raw_id else None
        status = event_obj.get("event")  # "processed", "dropped", "delivered", "deferred", "bounce", "open", "click"

    # 4. Fallback/Generic single event payload for testing/direct calls
    elif "provider_message_id" in payload:
        provider_id = payload.get("provider_message_id")
        status = payload.get("status")

    if not provider_id or not status:
        logger.warning("webhook_parse_failed", payload=payload)
        return False

    # Query campaign matching provider_message_id
    query = select(OutreachCampaign).where(OutreachCampaign.provider_message_id == provider_id)
    res = await db.execute(query)
    campaign = res.scalar_one_or_none()

    if not campaign:
        logger.warning("campaign_not_found_for_webhook", provider_message_id=provider_id)
        return False

    now = datetime.now(timezone.utc)
    updated = False

    status_lower = status.lower()

    if status_lower in ("delivered", "delivered_at"):
        campaign.delivered_at = now
        updated = True
    elif status_lower in ("read", "opened", "open", "click", "opened_at"):
        if not campaign.delivered_at:
            campaign.delivered_at = now
        campaign.opened_at = now
        updated = True
    elif status_lower in ("converted", "converted_at"):
        if not campaign.delivered_at:
            campaign.delivered_at = now
        if not campaign.opened_at:
            campaign.opened_at = now
        campaign.converted_at = now
        updated = True

    if updated:
        await db.commit()
        logger.info(
            "campaign_status_updated_via_webhook",
            campaign_id=str(campaign.id),
            provider_id=provider_id,
            status=status,
        )
        return True

    return False
