import os
import httpx
import uuid
import structlog
from jinja2 import Environment, FileSystemLoader
from shared.config.settings import get_settings
from shared.exceptions.domain import OutreachDispatchError
from services.gateway.middleware.pii_mask import PIIMasker

logger = structlog.get_logger(__name__)


def format_currency(value) -> str:
    """Formatter to display numbers as Indian currency formatting."""
    try:
        val = float(value)
        return f"{val:,.2f}"
    except (ValueError, TypeError):
        return str(value)


# Setup Jinja2 Environment
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
env.filters["format_currency"] = format_currency


async def send_email(
    campaign_id: str,
    email: str,
    message_body: str,
    session_id: str,
    redis,
    template_name: str | None = None,
    template_context: dict | None = None,
) -> str:
    """
    Sends email via SendGrid.
    Unmasks email immediately before API call.
    Renders message via Jinja2 template if template_name is provided.
    """
    settings = get_settings()

    # Unmask the email using PIIMasker
    masker = PIIMasker(redis_client=redis)
    unmasked_email = await masker.unmask(email, session_id)

    if not unmasked_email or unmasked_email.startswith("[EMAIL_"):
        raise OutreachDispatchError(campaign_id, "email", "Email unmasking failed or value is missing")

    # If template is specified, render it
    rendered_text = message_body
    if template_name:
        try:
            template = env.get_template(template_name)
            context = template_context or {}
            # Ensure safe defaults for template rendering
            context.setdefault("bank_name", "RM Copilot Private Banking")
            context.setdefault("advisory_tier", "Priority")
            rendered_text = template.render(**context)
        except Exception as exc:
            logger.error("email_template_rendering_failed", campaign_id=campaign_id, error=str(exc))
            # Fall back to using message_body as is

    # Convert plaintext/Jinja2 output into a clean HTML format
    html_body = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Exclusive Banking Update</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background-color: #f9fafb;
      color: #111827;
      margin: 0;
      padding: 40px 20px;
    }}
    .container {{
      max-width: 600px;
      background-color: #ffffff;
      padding: 40px;
      margin: 0 auto;
      border-radius: 8px;
      border: 1px solid #e5e7eb;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
    }}
    .logo {{
      font-size: 20px;
      font-weight: 700;
      color: #f59e0b;
      margin-bottom: 24px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}
    .content {{
      line-height: 1.6;
      font-size: 16px;
      white-space: pre-wrap;
    }}
    .footer {{
      margin-top: 32px;
      font-size: 12px;
      color: #6b7280;
      border-top: 1px solid #f3f4f6;
      padding-top: 16px;
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="logo">RM Copilot</div>
    <div class="content">{rendered_text}</div>
    <div class="footer">
      This is a confidential communication intended solely for the recipient. If you did not intend to receive this, please disregard.
    </div>
  </div>
</body>
</html>"""

    # If credentials are not configured, simulate success with a mock message ID
    if not settings.SENDGRID_API_KEY or not settings.SENDGRID_FROM_EMAIL:
        logger.info("email_dispatch_mocked", campaign_id=campaign_id, email="[MASKED]")
        return f"sg_{uuid.uuid4()}"

    url = "https://api.sendgrid.com/v3/mail/send"
    headers = {
        "Authorization": f"Bearer {settings.SENDGRID_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "personalizations": [
            {
                "to": [{"email": unmasked_email}],
                "subject": "Special pre-approved offer from your relationship manager"
            }
        ],
        "from": {"email": settings.SENDGRID_FROM_EMAIL},
        "content": [
            {
                "type": "text/html",
                "value": html_body
            }
        ]
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, headers=headers, timeout=10.0)
            if response.status_code >= 400:
                error_body = response.text
                logger.error("email_dispatch_failed", campaign_id=campaign_id, status=response.status_code, error=error_body)
                raise OutreachDispatchError(
                    campaign_id=campaign_id,
                    channel="email",
                    provider_error=f"HTTP {response.status_code}: {error_body}"
                )

            provider_message_id = response.headers.get("X-Message-Id", f"sg_{uuid.uuid4()}")
            logger.info("email_dispatch_success", campaign_id=campaign_id, provider_message_id=provider_message_id)
            return provider_message_id
    except httpx.HTTPError as exc:
        logger.error("email_dispatch_http_error", campaign_id=campaign_id, error=str(exc))
        raise OutreachDispatchError(
            campaign_id=campaign_id,
            channel="email",
            provider_error=f"HTTP connection error: {str(exc)}"
        )
