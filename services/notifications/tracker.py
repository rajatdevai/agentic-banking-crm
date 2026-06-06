# Outreach delivery tracker.
# Updates outreach_campaigns table with delivery receipts received via provider webhooks.
# Tracks the full funnel: sent → delivered → opened → converted.
# Correlates provider_message_id from webhook payload to campaign_id in the DB.
# Conversion is marked when customer engages with the bank product within 30 days.

# TODO: implement delivery receipt processing and funnel tracking in Phase 8 (notifications layer)
