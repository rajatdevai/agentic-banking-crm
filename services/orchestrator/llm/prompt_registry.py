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
    PARSE_COPILOT_FILTERS = "parse_copilot_filters"


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
- Customer Name: {{ customer_name }}
- Persona: {{ persona_type | replace("_", " ") | title }}
- Life Situation: {{ event_type | replace("_", " ") | title }}
- Recommended Product: {{ product_type | replace("_", " ") | title }}

Situation Summary:
{{ explanation_summary }}

Generate a JSON response containing exactly two WhatsApp message options (each 120-180 words) with the following keys:
1. "option_a": A "Direct & Professional" variation. Polite, concise, and focused on the product value.
2. "option_b": A "Conversational & Advisory" variation. Empathetic, friendly, and advisory-focused first.

Each option must:
1. Open warmly using the customer's actual first name: Dear {{ customer_name.split()[0] }}, or Hello {{ customer_name.split()[0] }},
2. Reference their current life situation naturally and empathetically.
3. Introduce the product as a solution, not a sales pitch.
4. Mention a specific benefit or numerical example relevant to their situation (e.g. overdraft limits, interest costs, EMI flexibility).
5. Have a clear call to action (schedule a call, reply to this message).
6. Close professionally with the RM's actual name: {{ rm_name }} (Your Relationship Manager).

CRITICAL INSTRUCTIONS:
- You MUST use the actual customer first name ({{ customer_name.split()[0] }}) in the greeting. Do NOT write "Dear Valued Customer" or "Dear Customer" or use any generic greeting placeholders.
- You MUST sign off with the Relationship Manager's actual name ({{ rm_name }}). Do NOT write "Your Relationship Manager" or "Relationship Manager" or use any generic signature placeholders.
- Sound human and warm, not like a template.
- Use the actual customer context provided above.
- WhatsApp format: short paragraphs, occasional emoji (professional context only).
- Return ONLY a valid JSON object. No explanation, no markdown fences.
""".strip(),

    # -------------------------------------------------------------------------
    # OUTREACH_SMS
    # Variables: same as WHATSAPP
    # -------------------------------------------------------------------------
    PromptKey.OUTREACH_SMS: """
You are writing an SMS from an {{ bank_name }} Relationship Manager to a customer.

Tone: {{ tone_guidelines }}
Customer Name: {{ customer_name }}
Situation: {{ event_type | replace("_", " ") | title }}
Product: {{ product_type | replace("_", " ") | title }}

Generate a JSON response containing exactly two SMS variations (each max 160 characters) with the following keys:
1. "option_a": A "Direct & Professional" variation. Brief, action-oriented, clear CTA.
2. "option_b": A "Conversational & Advisory" variation. Conversational, warm, helpful CTA.

Each option must:
- Address the customer by their actual first name ({{ customer_name.split()[0] }}).
- Reference their life situation in one phrase.
- One clear call to action.
- Sign with RM's actual name: {{ rm_name }}.

CRITICAL INSTRUCTIONS:
- You MUST use the actual customer first name ({{ customer_name.split()[0] }}) in the greeting. Do NOT write "Dear Valued Customer" or "Dear Customer" or use any generic greeting placeholders.
- You MUST sign off with the Relationship Manager's actual name ({{ rm_name }}). Do NOT write "Your Relationship Manager" or "Relationship Manager" or use any generic signature placeholders.

Return ONLY a valid JSON object. No explanation, no markdown fences.
""".strip(),

    # -------------------------------------------------------------------------
    # OUTREACH_EMAIL
    # Variables: same as WHATSAPP
    # -------------------------------------------------------------------------
    PromptKey.OUTREACH_EMAIL: """
You are writing a professional email from an {{ bank_name }} Relationship Manager.

Tone Guidelines:
{{ tone_guidelines }}

Customer Name: {{ customer_name }}
Customer Situation: {{ event_type | replace("_", " ") | title }}
Recommended Product: {{ product_type | replace("_", " ") | title }}
Summary: {{ explanation_summary }}

Generate a JSON response containing exactly two email variations with the following keys:
1. "option_a": A "Direct & Professional" variation. Clear subject line, concise structure.
2. "option_b": A "Conversational & Advisory" variation. Consultative subject line, warm narrative.

Each option must be formatted as a single string containing both subject and body in the following structure:
SUBJECT: [subject line]
BODY:
[email body]

Each email must:
- Use a professional greeting addressing the customer by their actual first name (e.g., Dear {{ customer_name.split()[0] }}).
- Have 2 short paragraphs: (1) acknowledge their life situation, (2) introduce product benefit with specifics.
- Have a clear call to action.
- Sign with RM's actual name ({{ rm_name }}), Title (Your Relationship Manager), and bank name ({{ bank_name }}).

CRITICAL INSTRUCTIONS:
- You MUST use the actual customer first name ({{ customer_name.split()[0] }}) in the greeting. Do NOT write "Dear Valued Customer" or "Dear Customer" or use any generic greeting placeholders.
- You MUST sign off with the Relationship Manager's actual name ({{ rm_name }}). Do NOT write "Your Relationship Manager" or "Relationship Manager" or use any generic signature placeholders.

Return ONLY a valid JSON object. No explanation, no markdown fences.
""".strip(),

    # -------------------------------------------------------------------------
    # PARSE_COPILOT_FILTERS
    # Variables: rm_question
    # -------------------------------------------------------------------------
    PromptKey.PARSE_COPILOT_FILTERS: """
You are an NLP parser for a banking CRM. Analyze the Relationship Manager's query and extract target filters as JSON.

Query: "{{ rm_question }}"

Available Persona Types:
- corporate_professional
- young_it_professional
- startup_founder
- doctor
- lawyer
- hni
- affluent_investor
- business_owner
- nri_family
- newly_married
- pre_retirement

Available Event Types:
- wedding
- home_purchase
- foreign_education
- child_education
- medical
- business_expansion
- promotion
- wealth_migration
- retirement_planning
- new_born

Available Product Types:
- personal_loan
- home_loan
- education_loan
- working_capital_loan
- loan_against_securities
- gold_loan
- wealth_advisory
- mutual_fund
- fixed_deposit
- forex_card
- current_account
- health_insurance
- child_education_plan
- premium_credit_card
- insurance
- business_credit_card

Extract the filters into this exact JSON schema:
{
  "persona_type": "one of the available persona types, or null",
  "event_type": "one of the available event types, or null",
  "product_type": "one of the available product types, or null",
  "time_window": "any mentioned time window like 'this month', 'last month', 'next 30 days', 'this quarter', or null",
  "is_pipeline_query": true/false (true if the query explicitly asks to find/search/score/run/recommend for a group of customers or portfolio)
}

Rules:
- If a filter is not mentioned or cannot be inferred, set it to null.
- Return ONLY the raw JSON block. No explanation, no markdown fences.
""".strip(),

    # -------------------------------------------------------------------------
    # RM_COPILOT_CONVERSATION
    # Variables: rm_question, portfolio_summary, rag_context,
    #            current_date, rm_name
    # -------------------------------------------------------------------------
    PromptKey.RM_COPILOT_CONVERSATION: """
You are RM Copilot, a highly strategic, elite Senior Relationship Manager and Private Banking advisor.
Today's date: {{ current_date }}

RM's Question:
"{{ rm_question }}"

Portfolio Summary (Actual CRM Customer Portfolio Data):
{{ portfolio_summary }}

Relevant Context (from product catalog & policy documents):
{{ rag_context }}

INSTRUCTIONS:
- You MUST answer the RM's question using the actual customer portfolio data provided above. Be specific, precise, and data-driven.
- If the RM asks about a specific customer (by name, description, or context), locate that customer's details in the Portfolio Summary (specifically their CIBIL score, monthly salary, account balance, total investments, liabilities, products held, active opportunities, and detected events). You MUST ground your entire response, strategic briefing steps, and suggested outreach directly in their actual numbers and context.
- Avoid general instructions. For instance, if a customer is a Startup Founder with high liabilities, customize the engagement steps and pitch to fit a startup owner's cashflow needs.
- Do NOT generate scripts with general placeholders like "[Customer Name]" or "[Product]" if the customer is identified in the query. Use their actual name and recommended product.
- You MUST format your response using standard Markdown using exactly these headers (do not skip headers; if a section is empty, explain why based on data):

### 📋 Executive Summary
A brief 2-3 sentence overview answering the question directly, incorporating high-level numbers or specific customer names.

### 🔍 Key Findings
Bullet points of the key facts, signals, or trends parsed from the data.

### 👤 Customer Insights
A structured breakdown of relevant customer(s). For each customer mentioned, include:
- **[Customer Name]** (Persona, CIBIL: [Score])
- **Conversion Probability:** [Score]%
- **Recommended Product:** [Product]
- **Positive Signals:** [Evidence or transaction events]
- **Risk Flags:** [Any risk tier or existing liability warnings]

### ⚠️ Risk Flags
A summary of risk factors (e.g. high debt, missed EMIs, low credit score, high risk tier) for the relevant customers.

### ⚡ Recommended Actions
Actionable, concrete next steps for the RM (e.g. "Call within 48 hours to discuss X").

### 💬 Suggested Outreach
A short, personalized message draft (WhatsApp or SMS) tailored to the client's detected events and needs, ready to copy-paste.

Rules:
- Never write long essay-style paragraphs. Keep it clean, structured, and easy to scan like ChatGPT.
- Do NOT include any fictitious customer data. Only use information provided in the Portfolio Summary and RAG Context.
- Do NOT include raw phone numbers, PAN, or account numbers.
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
