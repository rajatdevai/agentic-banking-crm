import httpx
import uuid
import structlog
from shared.config.settings import get_settings
from shared.exceptions.domain import OutreachDispatchError
from services.gateway.middleware.pii_mask import PIIMasker

logger = structlog.get_logger(__name__)


async def send_whatsapp(
    campaign_id: str,
    phone: str,
    message_body: str,
    session_id: str,
    redis,
) -> str:
    """
    Sends WhatsApp message via Twilio's WhatsApp Sandbox API.
    Unmasks phone immediately before API call.
    """
    settings = get_settings()

    # Unmask the phone number using PIIMasker
    masker = PIIMasker(redis_client=redis)
    unmasked_phone = await masker.unmask(phone, session_id)

    if not unmasked_phone or unmasked_phone.startswith("[PHONE_"):
        raise OutreachDispatchError(campaign_id, "whatsapp", "Phone number unmasking failed or value is missing")

    # Clean the unmasked phone number and ensure country code format
    cleaned_phone = unmasked_phone.strip()
    if not cleaned_phone.startswith("+"):
        # Default to India (+91) if not specified and matches Indian mobile length, otherwise prepend +
        if len(cleaned_phone) == 10:
            cleaned_phone = f"+91{cleaned_phone}"
        else:
            cleaned_phone = f"+{cleaned_phone}"

    # Verify credentials. If empty, simulate success with a mock message ID.
    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
        logger.info("whatsapp_dispatch_mocked", campaign_id=campaign_id, phone="[MASKED]")
        return f"wa_{uuid.uuid4()}"

    # Default to standard sandbox number if not configured
    from_number = settings.WHATSAPP_PHONE_NUMBER_ID or "+14155238886"
    if not from_number.startswith("whatsapp:"):
        from_number = f"whatsapp:{from_number}"

    to_number = f"whatsapp:{cleaned_phone}"

    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}/Messages.json"
    auth = (settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    data = {
        "To": to_number,
        "From": from_number,
        "Body": message_body,
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, data=data, auth=auth, timeout=10.0)
            if response.status_code >= 400:
                error_body = response.text
                logger.error("whatsapp_dispatch_failed", campaign_id=campaign_id, status=response.status_code, error=error_body)
                raise OutreachDispatchError(
                    campaign_id=campaign_id,
                    channel="whatsapp",
                    provider_error=f"HTTP {response.status_code}: {error_body}"
                )

            res_data = response.json()
            provider_message_id = res_data.get("sid", f"wa_{uuid.uuid4()}")
            logger.info("whatsapp_dispatch_success", campaign_id=campaign_id, provider_message_id=provider_message_id)
            return provider_message_id
    except httpx.HTTPError as exc:
        logger.error("whatsapp_dispatch_http_error", campaign_id=campaign_id, error=str(exc))
        raise OutreachDispatchError(
            campaign_id=campaign_id,
            channel="whatsapp",
            provider_error=f"HTTP connection error: {str(exc)}"
        )
