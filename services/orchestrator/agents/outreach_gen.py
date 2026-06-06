# Outreach Generation Agent — second agent that uses an LLM (gpt-4o).
# Retrieves persona-appropriate tone guidance from RAG (persona_playbooks/).
# Generates personalized WhatsApp/Email/SMS message drafts for each opportunity.
# Validates output: no raw PII, message length within channel limits,
# includes required regulatory disclosures, soft-sell tone for HNI personas.
# All messages are previews — dispatch only happens after RM explicit approval.

# TODO: implement OutreachGenAgent with RAG + gpt-4o in Phase 5 (orchestrator layer)
