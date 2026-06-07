"""
ProductRecAgent — RAG-backed product recommendation with persona-specific rules.

Reads: detected_events, customer_profile from state
Writes: recommended_products (list[ProductRecommendation])

For each event, retrieves eligible product documentation from the RAG knowledge base
(product_catalog collection) via hybrid vector search. Filters out already-held products
and applies persona-specific guardrails.

Persona-specific rules:
    - Never recommend personal loan to DTI > 50%
    - Always recommend wealth advisory alongside any product for HNI persona
    - Education loan only for young personas with active education signals
"""

from __future__ import annotations

from typing import Optional

import structlog

from services.orchestrator.agents.base_agent import BaseAgent
from services.orchestrator.graph.state import AgentState
from shared.constants.enums import EventType, PersonaType, ProductType
from shared.models.agent_state import (
    CustomerProfile,
    DetectedEvent,
    ProductRecommendation,
    RAGCitation,
)

logger = structlog.get_logger(__name__)

# Personas considered HNI (High Net Worth) — always recommend wealth advisory
_HNI_PERSONAS = {PersonaType.CORPORATE_PROFESSIONAL, PersonaType.STARTUP_FOUNDER}

# Young personas eligible for education loan
_YOUNG_PERSONAS = {PersonaType.YOUNG_IT_PROFESSIONAL}


class ProductRecAgent(BaseAgent):
    agent_name = "ProductRecAgent"
    timeout_seconds = 20.0

    async def execute(self, state: AgentState) -> dict:
        events: list[DetectedEvent] = state.get("detected_events") or []
        cp: Optional[CustomerProfile] = state.get("customer_profile")

        if not events or not cp:
            return {"recommended_products": []}

        recommendations: list[ProductRecommendation] = []
        seen_products: set[ProductType] = set()

        for event in events:
            # Get products for this event from opportunity scoring's mapping
            from services.orchestrator.agents.opportunity_scoring import _EVENT_PRODUCT_MAP
            products = _EVENT_PRODUCT_MAP.get(event.event_type, [])

            for product in products:
                if product in seen_products:
                    continue
                if cp.holds_product(product):
                    continue

                # --- Persona-specific guardrails ---
                if product == ProductType.PERSONAL_LOAN:
                    dti = cp.debt_to_income_ratio()
                    if dti and dti > 0.50:
                        logger.info(
                            "product_filtered_dti",
                            product=product.value,
                            dti=dti,
                        )
                        continue

                if product == ProductType.EDUCATION_LOAN:
                    if cp.persona_type not in _YOUNG_PERSONAS:
                        continue

                # --- RAG retrieval for this product ---
                citations: list[RAGCitation] = []
                eligibility_rationale = ""

                try:
                    from services.orchestrator.tools.vector_tools import hybrid_search
                    query = f"{product.value.replace('_', ' ')} eligibility criteria banking India"
                    rag_results = await hybrid_search(
                        query=query,
                        collection="product_catalog",
                        top_k=3,
                        db=self._db,
                        redis_client=self._redis,
                    )
                    for chunk in rag_results:
                        citations.append(RAGCitation(
                            chunk_id=chunk.get("id", ""),
                            doc_type="product_catalog",
                            relevance_score=chunk.get("score", 0.0),
                            excerpt=chunk.get("content", "")[:200],
                        ))
                    if citations:
                        eligibility_rationale = citations[0].excerpt

                except Exception as rag_exc:
                    logger.warning("rag_retrieval_failed", error=str(rag_exc), product=product.value)
                    eligibility_rationale = f"Standard {product.value.replace('_', ' ')} eligibility criteria apply."

                persona_notes = ""
                if cp.persona_type in _HNI_PERSONAS:
                    persona_notes = "HNI customer — premium relationship approach recommended."

                rec = ProductRecommendation(
                    product_type=product,
                    event_type=event.event_type,
                    eligibility_rationale=eligibility_rationale,
                    citations=citations,
                    persona_specific_notes=persona_notes,
                )
                recommendations.append(rec)
                seen_products.add(product)

        # HNI persona rule: always include wealth advisory if not already there
        if cp.persona_type in _HNI_PERSONAS:
            if ProductType.WEALTH_ADVISORY not in seen_products and not cp.holds_product(ProductType.WEALTH_ADVISORY):
                recommendations.append(ProductRecommendation(
                    product_type=ProductType.WEALTH_ADVISORY,
                    event_type=events[0].event_type,
                    eligibility_rationale="HNI persona — wealth advisory is always recommended as an overlay.",
                    citations=[],
                    persona_specific_notes="Standard HNI overlay — applies regardless of primary event.",
                ))

        logger.info(
            "product_rec_complete",
            customer_id=cp.customer_id,
            products_count=len(recommendations),
        )

        return {"recommended_products": recommendations}
