# Outreach Dispatch Task — sends a single outreach campaign message.
#
# Given a campaign_id:
#     1. Load outreach_campaign row from DB
#     2. Check DND registry and Redis daily rate limits via compliance.can_send()
#     3. Extract masked token from PII vault for the recipient
#     4. Call appropriate notification provider (WhatsApp / SMS / Email)
#     5. Update outreach_campaigns.sent_at and provider_message_id on success
#     6. Increment rate limits via compliance.increment_rate_limit()

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import structlog

from services.workers.celery_app import app

logger = structlog.get_logger(__name__)


@app.task(
    name="services.workers.tasks.outreach_dispatch.dispatch_campaign",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    soft_time_limit=30,
    time_limit=36,
    acks_late=True,
)
def dispatch_campaign(self, campaign_id: str):
    """Celery task entry point."""
    try:
        return asyncio.run(_dispatch_async(campaign_id))
    except Exception as exc:
        logger.error("outreach_dispatch_failed", campaign_id=campaign_id, error=str(exc))
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


async def _dispatch_async(campaign_id: str) -> dict:
    """Async dispatch implementation."""
    from sqlalchemy import select
    from shared.db.models import OutreachCampaign, Opportunity, Customer
    from services.notifications.compliance import can_send, increment_rate_limit
    from services.notifications.providers.whatsapp import send_whatsapp
    from services.notifications.providers.sms import send_sms
    from services.notifications.providers.email import send_email
    from services.gateway.middleware.pii_mask import PIIMasker

    db, redis = await _get_dependencies()

    try:
        # Step 1: Load campaign
        result = await db.execute(
            select(OutreachCampaign).where(OutreachCampaign.id == uuid.UUID(campaign_id))
        )
        campaign = result.scalar_one_or_none()
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")

        # Load opportunity and customer
        opp_result = await db.execute(
            select(Opportunity).where(Opportunity.id == campaign.opportunity_id)
        )
        opportunity = opp_result.scalar_one_or_none()
        if not opportunity:
            raise ValueError(f"Opportunity not found for campaign {campaign_id}")

        cust_result = await db.execute(
            select(Customer).where(Customer.id == opportunity.customer_id)
        )
        customer = cust_result.scalar_one_or_none()
        if not customer:
            raise ValueError(f"Customer not found for opportunity {opportunity.customer_id}")

        channel = campaign.channel.value
        session_id = campaign.session_id or str(uuid.uuid4())

        real_phone = customer.phone
        real_email = customer.email
        identifier = real_phone if channel in ("whatsapp", "sms") else real_email

        if not identifier:
            logger.error("outreach_missing_contact_info", campaign_id=campaign_id, channel=channel)
            return {"status": "failed_missing_contact", "campaign_id": campaign_id}

        # Step 2: DND and Rate Limit check
        if not await can_send(channel, identifier, db, redis):
            logger.info("outreach_blocked_compliance", campaign_id=campaign_id, channel=channel)
            return {"status": "blocked_compliance", "campaign_id": campaign_id}

        # Step 3: Get or create masked token in PII vault
        masker = PIIMasker(redis_client=redis)
        vault = await masker.load_vault(session_id)

        masked_target = None
        if channel in ("whatsapp", "sms"):
            for k, v in vault.items():
                if v == real_phone:
                    masked_target = k
                    break
            if not masked_target:
                masked_target = f"[PHONE_{len(vault) + 1}]"
                await masker.store_vault(session_id, {masked_target: real_phone})
        else:
            for k, v in vault.items():
                if v == real_email:
                    masked_target = k
                    break
            if not masked_target:
                masked_target = f"[EMAIL_{len(vault) + 1}]"
                await masker.store_vault(session_id, {masked_target: real_email})

        # Step 4: Send via appropriate provider
        if channel == "whatsapp":
            provider_message_id = await send_whatsapp(
                campaign_id=campaign_id,
                phone=masked_target,
                message_body=campaign.message_body,
                session_id=session_id,
                redis=redis,
            )
        elif channel == "sms":
            provider_message_id = await send_sms(
                campaign_id=campaign_id,
                phone=masked_target,
                message_body=campaign.message_body,
                session_id=session_id,
                redis=redis,
            )
        elif channel == "email":
            template_context = {
                "customer_first_name": customer.name.split()[0] if customer.name else "Customer",
                "loan_amount": float(opportunity.revenue_potential or 500000.0),
                "interest_rate": 10.5,
                "rm_name": "Your Relationship Manager",
                "bank_name": "RM Copilot Private Banking",
            }
            provider_message_id = await send_email(
                campaign_id=campaign_id,
                email=masked_target,
                message_body=campaign.message_body,
                session_id=session_id,
                redis=redis,
                template_name="wedding_personal_loan.j2" if opportunity.product_recommended.value == "personal_loan" else None,
                template_context=template_context,
            )
        else:
            raise ValueError(f"Unknown channel: {channel}")

        # Step 5: Update campaign record
        campaign.sent_at = datetime.now(timezone.utc)
        campaign.provider_message_id = provider_message_id
        await db.commit()

        # Step 6: Increment send counter
        await increment_rate_limit(channel, identifier, redis)

        logger.info(
            "outreach_sent",
            campaign_id=campaign_id,
            channel=channel,
            provider_msg_id=provider_message_id,
        )
        return {
            "status": "sent",
            "campaign_id": campaign_id,
            "provider_message_id": provider_message_id,
        }

    finally:
        await db.close()


async def _get_dependencies():
    from shared.db.session import get_async_session
    import redis.asyncio as aioredis
    from shared.config.settings import get_settings

    settings = get_settings()
    redis_client = await aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    async for session in get_async_session():
        return session, redis_client
    return None, redis_client
