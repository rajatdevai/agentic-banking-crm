"""
Domain exception hierarchy for the RM Copilot platform.

All exceptions inherit from RMCopilotBaseException so callers can do a single
broad catch at service boundaries while still being able to handle specific cases.

Design rule: every exception carries enough structured context to be logged
meaningfully without needing to inspect tracebacks.
"""


class RMCopilotBaseException(Exception):
    """Root exception for all RM Copilot domain errors. Never raise this directly."""
    pass


class CustomerNotFoundError(RMCopilotBaseException):
    """Raised when a requested customer_id does not exist or is not in the RM's portfolio."""

    def __init__(self, customer_id: str, rm_id: str | None = None):
        self.customer_id = customer_id
        self.rm_id = rm_id
        detail = f"Customer '{customer_id}' not found"
        if rm_id:
            detail += f" in portfolio of RM '{rm_id}'"
        super().__init__(detail)


class UnauthorizedAccessError(RMCopilotBaseException):
    """
    Raised when an RM attempts to access a customer that belongs to another RM.
    This is a security boundary — row-level security in PostgreSQL is the primary
    enforcement, but this exception is the application-layer guard.
    """

    def __init__(self, rm_id: str, customer_id: str):
        self.rm_id = rm_id
        self.customer_id = customer_id
        super().__init__(
            f"RM '{rm_id}' attempted unauthorized access to customer '{customer_id}'"
        )


class PIIDetectedInOutputError(RMCopilotBaseException):
    """
    Critical safety exception — raised when PII is detected in LLM output after
    de-masking validation. This should never happen if the masking pipeline is
    working correctly. Triggers an immediate alert and blocks the response from
    reaching the RM.
    """

    def __init__(self, agent_name: str, pii_entities: list[str]):
        self.agent_name = agent_name
        self.pii_entities = pii_entities
        super().__init__(
            f"PII detected in output from agent '{agent_name}'. "
            f"Entities found: {pii_entities}. Response blocked."
        )


class AgentExecutionError(RMCopilotBaseException):
    """
    Raised when an agent fails during execution after exhausting all retries.
    Carries the agent name and structured reason for logging and alerting.
    """

    def __init__(self, agent_name: str, reason: str, original_error: Exception | None = None):
        self.agent_name = agent_name
        self.reason = reason
        self.original_error = original_error
        super().__init__(f"Agent '{agent_name}' failed: {reason}")


class LLMUnavailableError(RMCopilotBaseException):
    """
    Raised when the OpenAI API is unreachable or returns a non-retryable error.
    Agents that depend on LLM calls should fall back to rule-based responses
    where possible, or surface this error to the RM with a clear message.
    """

    def __init__(self, provider: str = "openai", status_code: int | None = None):
        self.provider = provider
        self.status_code = status_code
        detail = f"LLM provider '{provider}' is unavailable"
        if status_code:
            detail += f" (HTTP {status_code})"
        super().__init__(detail)


class ScoringModelNotLoadedError(RMCopilotBaseException):
    """
    Raised when the ML model server cannot load a model artifact at startup
    or when an inference call is made before the model is ready.
    The Opportunity Scoring Agent falls back to heuristic scoring when this fires.
    """

    def __init__(self, model_name: str, path: str | None = None):
        self.model_name = model_name
        self.path = path
        detail = f"Scoring model '{model_name}' is not loaded"
        if path:
            detail += f" (expected at: {path})"
        super().__init__(detail)


class OutreachDispatchError(RMCopilotBaseException):
    """
    Raised when message dispatch to a notification provider fails after retries.
    Carries the campaign_id, channel, and provider error details for the audit log.
    """

    def __init__(self, campaign_id: str, channel: str, provider_error: str):
        self.campaign_id = campaign_id
        self.channel = channel
        self.provider_error = provider_error
        super().__init__(
            f"Outreach dispatch failed for campaign '{campaign_id}' "
            f"via {channel}: {provider_error}"
        )
