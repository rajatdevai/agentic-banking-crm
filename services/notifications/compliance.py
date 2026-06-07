# Outreach compliance checks — mandatory before any message dispatch.
# Checks performed:
#   1. DND registry: customer's number must not be on TRAI DND list
#   2. Opt-out registry: customer has not opted out of marketing communications
#   3. Contact frequency: channel-level daily send limits tracked in Redis
# Enforces channel-level daily limits:
#   - WhatsApp: 1000 messages per day per phone ID
#   - SMS: 500 messages per day
#   - Email: 2000 messages per day

from __future__ import annotations

from datetime import datetime, timezone
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.models import DNDRegistry

# Daily limits per channel
LIMITS = {
    "whatsapp": 1000,
    "sms": 500,
    "email": 2000,
}


async def is_opted_out(phone: str | None, email: str | None, db: AsyncSession) -> bool:
    """Check if a phone number or email is in the DND registry database."""
    if not phone and not email:
        return False

    conditions = []
    if phone:
        conditions.append(DNDRegistry.phone == phone)
    if email:
        conditions.append(DNDRegistry.email == email)

    query = sa.select(DNDRegistry).where(sa.or_(*conditions))
    res = await db.execute(query)
    record = res.scalar_one_or_none()
    return record is not None


async def check_rate_limit(channel: str, identifier: str, redis) -> bool:
    """Check if the daily send limit for a channel + identifier is within limits."""
    if not redis:
        return True

    channel_key = channel.lower()
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"rate_limit:{channel_key}:{identifier}:{date_str}"

    count_str = await redis.get(key)
    count = int(count_str) if count_str else 0

    limit = LIMITS.get(channel_key, 1000)
    return count < limit


async def increment_rate_limit(channel: str, identifier: str, redis) -> int:
    """Increment the rate limit count for a channel + identifier."""
    if not redis:
        return 1

    channel_key = channel.lower()
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"rate_limit:{channel_key}:{identifier}:{date_str}"

    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.expire(key, 86400)  # 24 hours expiry
        res = await pipe.execute()
        return res[0]


async def can_send(channel: str, identifier: str, db: AsyncSession, redis) -> bool:
    """Check both DND registry and Redis rate limits before sending."""
    phone = identifier if channel.lower() in ("whatsapp", "sms") else None
    email = identifier if channel.lower() == "email" else None

    # 1. Check DND / Opt-out registry
    if await is_opted_out(phone, email, db):
        return False

    # 2. Check channel daily limits in Redis
    if not await check_rate_limit(channel, identifier, redis):
        return False

    return True
