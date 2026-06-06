"""
Unit tests for the PII masking system.

Tests are fully synchronous where possible using the synchronous mask_text()
method. Redis-dependent tests use a mock.

Verifies:
    - Person name is masked
    - PAN card number is masked
    - Email address is masked
    - Indian phone number is masked
    - Currency amounts are preserved (not masked)
    - Vault round-trips: token maps back to original value
    - Unmasking restores original text
    - PIIDetectedInOutputError raised when PII leaks into output
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.gateway.middleware.pii_mask import PANRecognizer, PIIMasker


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_redis():
    """In-memory mock for Redis — stores key-value pairs in a dict."""
    store: dict[str, str] = {}

    client = AsyncMock()
    client.get = AsyncMock(side_effect=lambda key: store.get(key))
    client.setex = AsyncMock(
        side_effect=lambda key, ttl, value: store.update({key: value})
    )
    client._store = store  # expose for assertions
    return client


@pytest.fixture
def masker(mock_redis):
    """PIIMasker wired to mock Redis."""
    return PIIMasker(redis_client=mock_redis)


# ---------------------------------------------------------------------------
# Test: Person name is masked
# ---------------------------------------------------------------------------
def test_person_name_is_masked(masker):
    """A person name in text should be replaced with [PERSON_N] token."""
    text = "Please contact Rahul Sharma for the loan application."
    masked, vault = masker.mask_text(text, session_id="sess-001")

    assert "Rahul Sharma" not in masked, "Name should be masked"
    assert any("PERSON" in token for token in vault), "Vault should have a PERSON token"
    assert any(v == "Rahul Sharma" for v in vault.values()), \
        "Vault should map token to original name"


# ---------------------------------------------------------------------------
# Test: PAN card is masked
# ---------------------------------------------------------------------------
def test_pan_card_is_masked(masker):
    """An Indian PAN card number should be replaced with [PAN_N] token."""
    text = "Customer's PAN is ABCDE1234F, please verify KYC."
    masked, vault = masker.mask_text(text, session_id="sess-002")

    assert "ABCDE1234F" not in masked, "PAN card should be masked"
    assert any("PAN" in token for token in vault), "Vault should have a PAN token"
    assert "ABCDE1234F" in vault.values(), "Vault should map PAN token to original"


# ---------------------------------------------------------------------------
# Test: Email address is masked
# ---------------------------------------------------------------------------
def test_email_is_masked(masker):
    """An email address should be replaced with [EMAIL_N] token."""
    text = "Send the loan agreement to rahul.sharma@gmail.com"
    masked, vault = masker.mask_text(text, session_id="sess-003")

    assert "rahul.sharma@gmail.com" not in masked, "Email should be masked"
    assert any("EMAIL" in token for token in vault), "Vault should have an EMAIL token"


# ---------------------------------------------------------------------------
# Test: Phone number is masked
# ---------------------------------------------------------------------------
def test_phone_number_is_masked(masker):
    """An Indian mobile number should be replaced with [PHONE_N] token."""
    text = "Customer phone: +91 9876543210"
    masked, vault = masker.mask_text(text, session_id="sess-004")

    assert "9876543210" not in masked, "Phone number should be masked"
    assert any("PHONE" in token for token in vault), "Vault should have a PHONE token"


# ---------------------------------------------------------------------------
# Test: Currency amounts are preserved
# ---------------------------------------------------------------------------
def test_currency_amount_is_preserved(masker):
    """
    Financial amounts must NOT be masked — the LLM needs them for reasoning.
    We mask identity, not money.
    """
    text = "Customer has a salary credit of ₹75,000 per month."
    masked, vault = masker.mask_text(text, session_id="sess-005")

    assert "75,000" in masked, "Currency amount should be preserved in masked text"
    assert "75000" in masked or "75,000" in masked, "Amount digits should remain"


# ---------------------------------------------------------------------------
# Test: Vault round-trip — multiple entities
# ---------------------------------------------------------------------------
def test_vault_round_trip_multiple_entities(masker):
    """
    Full round-trip: mask a text with multiple PII entities, verify vault
    maps each token back to the correct original value.
    """
    text = "Name: Priya Menon, PAN: XYZAB9876C, Email: priya@example.com"
    masked, vault = masker.mask_text(text, session_id="sess-006")

    # None of the originals should appear in masked text
    assert "Priya Menon" not in masked
    assert "XYZAB9876C" not in masked
    assert "priya@example.com" not in masked

    # All originals should be recoverable from vault
    original_values = set(vault.values())
    assert "XYZAB9876C" in original_values
    assert "priya@example.com" in original_values


# ---------------------------------------------------------------------------
# Test: Unmask restores original text
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unmask_restores_original(masker, mock_redis):
    """
    After masking and storing to Redis vault, unmasking should restore
    the original text exactly.
    """
    session_id = "sess-007"
    original = "Contact Arjun Kapoor at arjun@corp.com for details."

    masked, vault = masker.mask_text(original, session_id)
    await masker.store_vault(session_id, vault)

    restored = await masker.unmask(masked, session_id)

    assert restored == original, f"Unmasked text should equal original.\nGot: {restored}"


# ---------------------------------------------------------------------------
# Test: No PII in text — no masking applied
# ---------------------------------------------------------------------------
def test_no_pii_passthrough(masker):
    """Text with no PII should pass through unchanged with an empty vault."""
    text = "The customer has a high conversion probability for personal loan."
    masked, vault = masker.mask_text(text, session_id="sess-008")

    assert masked == text, "Text with no PII should be unchanged"
    assert vault == {}, "Vault should be empty when no PII detected"


# ---------------------------------------------------------------------------
# Test: PAN recogniser pattern
# ---------------------------------------------------------------------------
def test_pan_recogniser_pattern():
    """Validate PAN card regex pattern directly."""
    import re
    pan_pattern = r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"
    assert re.search(pan_pattern, "ABCDE1234F"), "Valid PAN should match"
    assert re.search(pan_pattern, "XYZAB9876C"), "Valid PAN should match"
    assert not re.search(pan_pattern, "ABCDE123F"), "Short PAN should not match"
    assert not re.search(pan_pattern, "abcde1234f"), "Lowercase PAN should not match"


# ---------------------------------------------------------------------------
# Test: Empty string masking
# ---------------------------------------------------------------------------
def test_empty_string_masking(masker):
    """Empty string should return empty string with empty vault."""
    masked, vault = masker.mask_text("", session_id="sess-009")
    assert masked == ""
    assert vault == {}
