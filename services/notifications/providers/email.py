# Email provider using SendGrid or AWS SES.
# Sends HTML email outreach for HNI customers and formal product communications.
# Uses Jinja2 templates rendered at dispatch time with customer-specific variables.
# Tracks: sent_at, delivered_at, opened_at (via pixel tracking), click events.
# Unsubscribe link is mandatory in every email (CAN-SPAM / TRAI compliance).

# TODO: implement email provider with SendGrid client in Phase 8 (notifications layer)
