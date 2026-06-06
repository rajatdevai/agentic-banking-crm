# Outreach compliance checks — mandatory before any message dispatch.
# Checks performed:
#   1. DND registry: customer's number must not be on TRAI DND list
#   2. Opt-out registry: customer has not opted out of marketing communications
#   3. Contact frequency: max 3 outreach attempts per product per customer per month
#   4. Contact window: only send between 9 AM and 9 PM IST (RBI guideline)
#   5. Channel consent: customer has explicitly opted in to WhatsApp marketing
# Raises ComplianceViolationError if any check fails — dispatch is blocked.

# TODO: implement compliance check pipeline in Phase 8 (notifications layer)
