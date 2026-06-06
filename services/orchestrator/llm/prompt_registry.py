"""
Prompt Registry — every prompt in the system lives here as a named Jinja2 template.

Rules:
    - NO prompt string is ever written inline inside an agent file
    - ALL prompts are Jinja2 templates with typed variable names documented in comments
    - Never use f-strings for prompt construction — always render through Jinja2
    - Templates are validated at import time (syntax check only)

Usage:
    from services.orchestrator.llm.prompt_registry import render_prompt, PromptKey

    prompt = render_prompt(
        PromptKey.EXPLAINABILITY,
        persona_type="corporate_professional",
        event_type="wedding",
        ...
    )
"""

from __future__ import annotations

from enum import Enum

from jinja2 import BaseLoader, Environment, TemplateSyntaxError

_jinja_env = Environment(loader=BaseLoader())


class PromptKey(str, Enum):
    """Registry of all prompt template keys."""
    CUSTOMER_SUMMARY = "customer_summary"
    EXPLAINABILITY = "explainability"
    PRODUCT_RECOMMENDATION_REASONING = "product_rec_reasoning"
    OUTREACH_WHATSAPP = "outreach_whatsapp"
    OUTREACH_SMS = "outreach_sms"
    OUTREACH_EMAIL = "outreach_email"
    RM_COPILOT_CONVERSATION = "rm_copilot"
    OUTPUT_SELF_CORRECTION = "output_self_correction"


# ---------------------------------------------------------------------------
# Template definitions
# ---------------------------------------------------------------------------

_TEMPLATES: dict[PromptKey, str] = {

    # -------------------------------------------------------------------------
    # CUSTOMER_SUMMARY
    # Variables: persona_type, salary_band, relationship_tenure_months,
    #            behavioral_tags (list), product_holdings (dict),
    #            credit_score, risk_tier
    # -------------------------------------------------------------------------
    PromptKey.CUSTOMER_SUMMARY: """
You are analysing a banking customer for a Relationship Manager.
Summarise the customer profile in 3-4 sentences, suitable for RM briefing.

Customer Profile:
- Persona: {{ persona_type | replace("_", " ") | title }}
- Salary Band: {{ salary_band }}
- Relationship Tenure: {{ relationship_tenure_months }} months
- Risk Tier: {{ risk_tier }}
- Credit Score: {{ credit_score if credit_score else "Not available" }}
- Behavioral Tags: {{ behavioral_tags | join(", ") if behavioral_tags else "None" }}
- Products Held: {{ product_holdings | list | join(", ") if product_holdings else "None" }}

Write a concise 3-4 sentence professional summary. Do not include any personal identifiers.
Do not use the customer's name. Refer to them as "this customer" or "the customer".
Output plain text only.
""".strip(),

    # -------------------------------------------------------------------------
    # EXPLAINABILITY
    # Variables: persona_type, salary_band, risk_tier, credit_score,
    #            event_type, event_confidence, signals_summary,
    #            product_recommended, conversion_probability,
    #            revenue_potential, relationship_tenure_months,
    #            behavioral_tags (list)
    # -------------------------------------------------------------------------
    PromptKey.EXPLAINABILITY: """
You are generating an explainability card for a Relationship Manager to understand
why a customer has been flagged as a high-priority opportunity.

Context:
- Customer Persona: {{ persona_type | replace("_", " ") | title }}
- Salary Band: {{ salary_band }}
- Relationship Tenure: {{ relationship_tenure_months }} months
- Risk Tier: {{ risk_tier }}
- Credit Score: {{ credit_score if credit_score else "Not disclosed" }}
- Behavioral Tags: {{ behavioral_tags | join(", ") }}

Detected Life Event:
- Event Type: {{ event_type | replace("_", " ") | title }}
- Confidence Score: {{ "%.0f"|format(event_confidence * 100) }}%
- Evidence: {{ signals_summary }}

Recommended Product: {{ product_recommended | replace("_", " ") | title }}
Estimated Conversion Probability: {{ "%.0f"|format(conversion_probability * 100) }}%
Estimated Revenue Potential: ₹{{ "{:,.0f}".format(revenue_potential) if revenue_potential else "Unknown" }}

Generate a JSON response with exactly these fields:
{
  "why_selected": "2-3 sentences explaining the signals that made this customer stand out",
  "event_explanation": "1-2 sentences on the life event detected and its significance",
  "product_rationale": "2-3 sentences on why this product fits the customer's current need",
  "conversion_reasoning": "1-2 sentences on why the conversion probability is at this level",
  "rm_action": "1-2 sentences on what the RM should do and in what timeframe"
}

Rules: No customer names. No PAN, Aadhaar, or account numbers. Professional tone.
""".strip(),

    # -------------------------------------------------------------------------
    # PRODUCT_RECOMMENDATION_REASONING
    # Variables: persona_type, event_type, product_type,
    #            eligibility_criteria (string from RAG), existing_products (list)
    # -------------------------------------------------------------------------
    PromptKey.PRODUCT_RECOMMENDATION_REASONING: """
Based on the following eligibility criteria retrieved from the product knowledge base,
explain in 2-3 sentences why {{ product_type | replace("_", " ") | title }} is the
right recommendation for a {{ persona_type | replace("_", " ") | title }} customer
who has a {{ event_type | replace("_", " ") }} life event.

Eligibility Criteria:
{{ eligibility_criteria }}

Products already held by this customer: {{ existing_products | join(", ") if existing_products else "None" }}

Write a concise professional rationale (2-3 sentences). Plain text only.
""".strip(),

    # -------------------------------------------------------------------------
    # OUTREACH_WHATSAPP
    # Variables: persona_type, event_type, product_type, explanation_summary,
    #            tone_guidelines, rm_name, bank_name
    # -------------------------------------------------------------------------
    PromptKey.OUTREACH_WHATSAPP: """
You are writing a WhatsApp message from an {{ bank_name }} Relationship Manager to a customer.

Tone Guidelines (from persona playbook):
{{ tone_guidelines }}

Customer Context:
- Persona: {{ persona_type | replace("_", " ") | title }}
- Life Situation: {{ event_type | replace("_", " ") | title }}
- Recommended Product: {{ product_type | replace("_", " ") | title }}

Situation Summary:
{{ explanation_summary }}

Write a WhatsApp message (120-180 words) that:
1. Opens warmly without using the customer's name (use "Dear Valued Customer" or leave unnamed)
2. References their current life situation naturally and empathetically
3. Introduces the product as a solution, not a sales pitch
4. Mentions a specific benefit relevant to their situation
5. Has a clear call to action (schedule a call, reply to this message)
6. Closes professionally with the RM's name: {{ rm_name }}

Constraints:
- NO personal identifiers (no names, PAN, phone numbers, account numbers)
- Sound human and warm, not like a template
- WhatsApp format: short paragraphs, occasional emoji (professional context only)
- Output the message text only — no JSON wrapper
""".strip(),

    # -------------------------------------------------------------------------
    # OUTREACH_SMS
    # Variables: same as WHATSAPP
    # -------------------------------------------------------------------------
    PromptKey.OUTREACH_SMS: """
You are writing an SMS from an {{ bank_name }} Relationship Manager to a customer.

Tone: {{ tone_guidelines }}
Situation: {{ event_type | replace("_", " ") | title }}
Product: {{ product_type | replace("_", " ") | title }}

Write an SMS (max 160 characters):
- Brief and action-oriented
- References their life situation in one phrase
- One clear call to action
- Sign with RM name: {{ rm_name }}
- NO personal identifiers

Output SMS text only.
""".strip(),

    # -------------------------------------------------------------------------
    # OUTREACH_EMAIL
    # Variables: same as WHATSAPP + subject_line
    # -------------------------------------------------------------------------
    PromptKey.OUTREACH_EMAIL: """
You are writing a professional email from an {{ bank_name }} Relationship Manager.

Tone Guidelines:
{{ tone_guidelines }}

Customer Situation: {{ event_type | replace("_", " ") | title }}
Recommended Product: {{ product_type | replace("_", " ") | title }}
Summary: {{ explanation_summary }}

Write a professional email with:
Subject: [Generate an appropriate subject line]
Body: 
  - Professional greeting (no personal name — use "Dear Valued Customer")
  - 2 short paragraphs: (1) acknowledge their life situation, (2) introduce product benefit
  - Clear call to action: schedule a call or reply
  - Professional sign-off with RM name ({{ rm_name }}) and bank name ({{ bank_name }})

Format:
SUBJECT: [subject line]
BODY:
[email body]

NO personal identifiers anywhere in the output.
""".strip(),

    # -------------------------------------------------------------------------
    # RM_COPILOT_CONVERSATION
    # Variables: rm_question, portfolio_summary, rag_context,
    #            current_date, rm_name
    # -------------------------------------------------------------------------
    PromptKey.RM_COPILOT_CONVERSATION: """
You are RM Copilot, an AI assistant for {{ rm_name }}, a banking Relationship Manager.
Today's date: {{ current_date }}

RM's Question:
{{ rm_question }}

Portfolio Summary:
{{ portfolio_summary }}

Relevant Context (from knowledge base):
{{ rag_context }}

Answer the RM's question based on the context provided. Be specific and actionable.
If the answer requires customer-specific data not in the context, say so clearly.
Format your response as if briefing a colleague — professional but conversational.
If recommending actions, be specific about timing and priority.
""".strip(),

    # -------------------------------------------------------------------------
    # OUTPUT_SELF_CORRECTION
    # Variables: original_output, expected_schema, validation_error
    # -------------------------------------------------------------------------
    PromptKey.OUTPUT_SELF_CORRECTION: """
The following JSON output failed validation. Fix it to match the expected schema.

Expected Schema:
{{ expected_schema }}

Validation Error:
{{ validation_error }}

Original Output (malformed):
{{ original_output }}

Return ONLY the corrected JSON. No explanation, no markdown code fences.
""".strip(),

}


# ---------------------------------------------------------------------------
# Validation at import time
# ---------------------------------------------------------------------------
def _validate_all_templates() -> None:
    """Catch Jinja2 syntax errors at startup rather than at call time."""
    env = Environment(loader=BaseLoader())
    for key, template_str in _TEMPLATES.items():
        try:
            env.parse(template_str)
        except TemplateSyntaxError as exc:
            raise RuntimeError(f"Prompt template {key} has a syntax error: {exc}") from exc


_validate_all_templates()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def render_prompt(key: PromptKey, **variables) -> str:
    """
    Render a named prompt template with the provided variables.

    Args:
        key: PromptKey enum value identifying the template
        **variables: Named variables matching the template's {{ ... }} placeholders

    Returns:
        Rendered prompt string, ready to send to the LLM router.

    Raises:
        KeyError: if key is not registered (shouldn't happen with enum)
        jinja2.UndefinedError: if a required template variable is missing
    """
    env = Environment(loader=BaseLoader())
    template = env.from_string(_TEMPLATES[key])
    return template.render(**variables)
