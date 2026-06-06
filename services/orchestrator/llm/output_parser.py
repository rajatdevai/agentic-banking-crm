"""
Output Parser — validates LLM responses against Pydantic schemas.

Two-phase validation:
    1. Parse raw LLM output as JSON and validate against the Pydantic model
    2. If phase 1 fails: send malformed output back to gpt-4o-mini with a
       self-correction prompt. One correction attempt only.
    3. If both fail: raise OutputParseError with raw output attached for debugging.

Usage:
    from services.orchestrator.llm.output_parser import parse_llm_output
    from my_module import MyResponseModel

    result: MyResponseModel = await parse_llm_output(
        raw_text=llm_output,
        response_model=MyResponseModel,
        session_id=state["session_id"],
    )
"""

from __future__ import annotations

import json
from typing import Type, TypeVar

import structlog
from pydantic import BaseModel, ValidationError

from services.orchestrator.llm.prompt_registry import PromptKey, render_prompt

logger = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


class OutputParseError(Exception):
    """Raised when LLM output cannot be parsed after self-correction attempt."""

    def __init__(self, message: str, raw_output: str, validation_error: str):
        self.raw_output = raw_output
        self.validation_error = validation_error
        super().__init__(message)


async def parse_llm_output(
    raw_text: str,
    response_model: Type[T],
    session_id: str = "unknown",
) -> T:
    """
    Parse and validate raw LLM text against a Pydantic response model.

    Phase 1: Direct parse — strip markdown fences, parse JSON, validate model.
    Phase 2: Self-correction — if phase 1 fails, ask gpt-4o-mini to fix the JSON.
    Raise: OutputParseError if both phases fail.
    """
    # Phase 1 — direct parse
    try:
        return _parse_direct(raw_text, response_model)
    except (json.JSONDecodeError, ValidationError) as phase1_error:
        logger.warning(
            "llm_output_parse_failed_phase1",
            model=response_model.__name__,
            error=str(phase1_error),
            session_id=session_id,
        )

    # Phase 2 — self-correction
    try:
        corrected = await _self_correct(
            raw_output=raw_text,
            response_model=response_model,
            validation_error=str(phase1_error),
            session_id=session_id,
        )
        result = _parse_direct(corrected, response_model)
        logger.info("llm_output_self_correction_success", model=response_model.__name__)
        return result
    except Exception as phase2_error:
        raise OutputParseError(
            message=(
                f"Failed to parse LLM output as {response_model.__name__} "
                f"after self-correction attempt."
            ),
            raw_output=raw_text,
            validation_error=str(phase2_error),
        ) from phase2_error


def _parse_direct(raw_text: str, response_model: Type[T]) -> T:
    """
    Strip markdown fences, parse JSON, validate against the Pydantic model.
    Raises json.JSONDecodeError or pydantic.ValidationError on failure.
    """
    cleaned = raw_text.strip()

    # Strip markdown code fences if present
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first line (```json or ```) and last line (```)
        cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    data = json.loads(cleaned)
    return response_model.model_validate(data)


async def _self_correct(
    raw_output: str,
    response_model: Type[T],
    validation_error: str,
    session_id: str,
) -> str:
    """
    Send the malformed output back to gpt-4o-mini with a correction prompt.
    Returns the corrected raw text (not yet validated).
    """
    # Import here to avoid circular import
    from services.orchestrator.llm.router import get_llm_router

    schema_json = json.dumps(response_model.model_json_schema(), indent=2)
    correction_prompt = render_prompt(
        PromptKey.OUTPUT_SELF_CORRECTION,
        expected_schema=schema_json,
        validation_error=validation_error,
        original_output=raw_output[:2000],  # Truncate to avoid token explosion
    )

    corrected = await get_llm_router().call_fast(
        prompt=correction_prompt,
        system="You are a JSON formatting assistant. Fix the provided JSON to match the schema exactly.",
        temperature=0.0,
        session_id=session_id,
    )

    return corrected
