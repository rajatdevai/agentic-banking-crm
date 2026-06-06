# SMS provider using Twilio or MSG91.
# Sends SMS outreach for customers without WhatsApp opt-in.
# Message length: max 160 characters (single SMS). 320 chars uses 2 credits.
# DND check is mandatory before sending (TRAI regulations).
# Tracks: sent_at, provider_message_id, delivery status (via webhook callback).

# TODO: implement SMS provider with Twilio client in Phase 8 (notifications layer)
