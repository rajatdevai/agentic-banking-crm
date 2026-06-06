# Centralized prompt registry — all prompts versioned here, never inline in agent code.
# Each prompt is a named constant with a version tag and description.
# Prompts are Jinja2-style templates: variables are injected at call time.
# This registry enables: prompt A/B testing, audit trail of prompt changes,
# and rollback to prior prompt versions without code changes.

# TODO: populate with versioned prompt templates in Phase 5 (orchestrator layer)

PROMPTS: dict[str, dict] = {
    # Key: prompt_name, Value: {version, template, description}
    # e.g. "explainability_v1": {"version": 1, "template": "...", "description": "..."}
}
