# Event Detection Agent — runs in parallel with Transaction Intelligence Agent.
# Deterministic rule engine: NO LLM involved. Runs a set of rule definitions over
# recent transactions to detect life events: wedding signals (jewellery + banquet MCCs),
# home purchase (real estate + property registration payments), business expansion
# (GST payments spike + supplier transfers), foreign education (SWIFT + visa fees), etc.
# Each fired rule produces a DetectedEvent with a confidence score and evidence payload.
# Fully auditable: every detection can be traced to the exact rules that fired.

# TODO: implement EventDetectionAgent rule engine in Phase 5 (orchestrator layer)
