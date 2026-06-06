# Life event classifier — rule ensemble identifying which life event has occurred.
# This is NOT a pure ML model — it is a deterministic rule system with confidence scoring.
# Rules map MCC transaction patterns to event types:
#   WEDDING: jewellery MCCs (5094) + banquet/catering MCCs (5811, 7011) within 90 days
#   HOME_PURCHASE: property registration + real estate MCCs + large lump-sum transfer
#   FOREIGN_EDUCATION: SWIFT transfers + visa fee MCCs + education institution payments
#   BUSINESS_EXPANSION: GST spike + new beneficiary onboarding + supplier NEFT increase
#   PROMOTION: salary credit increase > 20% month-over-month for 2+ consecutive months
# Output: DetectedEvent with event_type, confidence_score, and signals evidence list.

# TODO: implement rule ensemble event classifier in Phase 5 (orchestrator layer)
