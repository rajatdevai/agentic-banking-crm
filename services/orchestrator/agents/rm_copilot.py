"""
RMCopilotAgent — RAG-backed conversational assistant (gpt-4o, streaming).

Reads: rm_question, rm_id from state
Writes: copilot_response_chunks (list[str] — tokens accumulated for SSE streaming)

Flow:
    1. Retrieve context from all four RAG collections in parallel
    2. Fetch RM's portfolio summary from DB (customer count, top personas, recent events)
    3. Construct masked prompt via Jinja2 prompt registry
    4. Stream response token-by-token back through copilot_response_chunks
       (gateway SSE endpoint consumes these chunks)
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import structlog
from sqlalchemy import func, select

from services.orchestrator.agents.base_agent import BaseAgent
from services.orchestrator.graph.state import AgentState
from services.orchestrator.llm.prompt_registry import PromptKey, render_prompt
from services.orchestrator.llm.router import get_llm_router
from shared.db.models import Customer, DetectedEvent as DetectedEventORM, Opportunity

logger = structlog.get_logger(__name__)

_RAG_COLLECTIONS = ["product_catalog", "policy_docs", "persona_playbooks", "market_context"]


class RMCopilotAgent(BaseAgent):
    agent_name = "RMCopilotAgent"
    timeout_seconds = 90.0   # Streaming can be longer

    async def execute(self, state: AgentState) -> dict:
        import asyncio
        import uuid
        from sqlalchemy import select
        from shared.db.models import Customer
        from services.orchestrator.graph.builder import build_scoring_graph
        from services.orchestrator.graph.state import initial_state as fresh_initial_state

        rm_question: Optional[str] = state.get("rm_question")
        rm_id: str = state.get("rm_id", "")
        session_id: str = state.get("session_id", "unknown")
        token_queue = state.get("token_queue")

        if not rm_question:
            return {"copilot_response_chunks": ["[No question provided]"]}

        # 1. Call LLM to parse conversational filters
        parsed_filters = {
            "persona_type": None,
            "event_type": None,
            "product_type": None,
            "time_window": None,
            "is_pipeline_query": False
        }

        try:
            filter_prompt = render_prompt(
                PromptKey.PARSE_COPILOT_FILTERS,
                rm_question=rm_question
            )
            
            raw_filters = await get_llm_router().call_primary(
                prompt=filter_prompt,
                system="You are an NLP entity extraction assistant. Output raw JSON only.",
                session_id=session_id,
                temperature=0.0,
            )
            
            cleaned_filters = raw_filters.strip()
            if cleaned_filters.startswith("```json"):
                cleaned_filters = cleaned_filters[7:]
            if cleaned_filters.endswith("```"):
                cleaned_filters = cleaned_filters[:-3]
            cleaned_filters = cleaned_filters.strip()
            
            import json
            parsed_filters.update(json.loads(cleaned_filters))
            logger.info("parsed_conversational_filters", filters=parsed_filters)
        except Exception as exc:
            logger.warning("failed_to_parse_conversational_filters", error=str(exc))
            # Fallback to naive string matching
            q_lower = rm_question.lower()
            is_pipe = (
                ("find" in q_lower or "search" in q_lower or "run" in q_lower or "recommend" in q_lower or "show" in q_lower or "filter" in q_lower or "identify" in q_lower)
                and ("loan" in q_lower or "product" in q_lower or "whatsapp" in q_lower or "outreach" in q_lower or "customer" in q_lower or "hni" in q_lower or "event" in q_lower)
            )
            parsed_filters["is_pipeline_query"] = is_pipe

        is_pipeline_query = parsed_filters.get("is_pipeline_query")

        if is_pipeline_query:
            if token_queue:
                await token_queue.put("🔍 *Initializing multi-agent customer scoring pipeline...*\n\n")
                await asyncio.sleep(0.1)

            # Determine transaction dates based on time_window
            txn_start_date = None
            txn_end_date = None
            
            time_window = parsed_filters.get("time_window")
            if time_window:
                tw_lower = time_window.lower()
                from datetime import datetime, timezone, timedelta
                import calendar
                today = datetime.now(timezone.utc)
                
                if "this month" in tw_lower or "current month" in tw_lower:
                    txn_start_date = datetime(today.year, today.month, 1, tzinfo=timezone.utc)
                    last_day = calendar.monthrange(today.year, today.month)[1]
                    txn_end_date = datetime(today.year, today.month, last_day, 23, 59, 59, tzinfo=timezone.utc)
                elif "last month" in tw_lower or "previous month" in tw_lower:
                    first_of_this_month = datetime(today.year, today.month, 1, tzinfo=timezone.utc)
                    last_of_last_month = first_of_this_month - timedelta(seconds=1)
                    txn_start_date = datetime(last_of_last_month.year, last_of_last_month.month, 1, tzinfo=timezone.utc)
                    txn_end_date = last_of_last_month
                elif "next 30 days" in tw_lower or "30 days" in tw_lower:
                    txn_start_date = today
                    txn_end_date = today + timedelta(days=30)
                elif "this quarter" in tw_lower:
                    quarter = (today.month - 1) // 3 + 1
                    q_start_month = 3 * (quarter - 1) + 1
                    txn_start_date = datetime(today.year, q_start_month, 1, tzinfo=timezone.utc)
                    q_end_month = q_start_month + 2
                    last_day = calendar.monthrange(today.year, q_end_month)[1]
                    txn_end_date = datetime(today.year, q_end_month, last_day, 23, 59, 59, tzinfo=timezone.utc)

            # 2. Fetch RM's customers
            try:
                stmt = select(Customer).where(
                    Customer.rm_id == rm_id,
                    Customer.deleted_at.is_(None)
                )
                
                # Apply persona filter if parsed
                p_type = parsed_filters.get("persona_type")
                if p_type:
                    from shared.constants.enums import PersonaType
                    try:
                        enum_persona = PersonaType(p_type)
                        stmt = stmt.where(Customer.persona_type == enum_persona)
                    except ValueError:
                        pass
                
                cust_result = await self._db.execute(stmt)
                customers = cust_result.scalars().all()
            except Exception as e:
                logger.error("failed_to_fetch_rm_customers", error=str(e))
                err_msg = f"Failed to retrieve customers from database: {str(e)}"
                if token_queue:
                    await token_queue.put(err_msg)
                return {"copilot_response_chunks": [err_msg], "errors": [err_msg]}

            if not customers:
                msg = "No active customers found in your portfolio matching the filters."
                if token_queue:
                    await token_queue.put(msg)
                return {"copilot_response_chunks": [msg]}

            if token_queue:
                await token_queue.put(f"⚙️ *Running LangGraph scoring graph for {len(customers)} customers in parallel...*\n\n")
                await asyncio.sleep(0.1)

            # 3 & 4. Invoke scoring graph for each customer with a fresh scoped DB session
            from shared.db.session import AsyncSessionLocal

            async def _score_single_customer(cust):
                async with AsyncSessionLocal() as cust_db:
                    cust_scoring_graph = build_scoring_graph(db=cust_db, redis=self._redis)
                    init_s = fresh_initial_state(
                        customer_id=str(cust.id),
                        customer_name=cust.name,
                        rm_id=rm_id,
                        rm_name=state.get("rm_name", ""),
                        session_id=session_id,
                        trace_id=str(uuid.uuid4())
                    )
                    if txn_start_date:
                        init_s["txn_start_date"] = txn_start_date
                    if txn_end_date:
                        init_s["txn_end_date"] = txn_end_date
                    return await cust_scoring_graph.ainvoke(init_s)

            tasks = [_score_single_customer(cust) for cust in customers]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 5. Synthesize results
            opportunities_found = []
            all_errors = []
            accumulated_trace = ["RMCopilotAgent"]

            for cust, res in zip(customers, results):
                if isinstance(res, Exception):
                    logger.error("customer_scoring_failed", customer_id=str(cust.id), error=str(res))
                    all_errors.append(f"Scoring failed for {cust.name}: {str(res)}")
                    continue
                
                state_errors = res.get("errors") or []
                if state_errors:
                    all_errors.extend(state_errors)

                trace = res.get("agent_trace") or []
                for t in trace:
                    if t not in accumulated_trace:
                        accumulated_trace.append(t)

                opps = res.get("opportunities") or []
                for opp in opps:
                    # Filter by parsed product_type
                    prod_filter = parsed_filters.get("product_type")
                    if prod_filter and opp.product_recommended.value != prod_filter:
                        continue
                        
                    # Filter by parsed event_type
                    ev_filter = parsed_filters.get("event_type")
                    if ev_filter and opp.event_type.value != ev_filter:
                        continue
                        
                    # Legacy fallback filters
                    q_lower = rm_question.lower()
                    if "personal loan" in q_lower and opp.product_recommended.value != "personal_loan":
                        continue
                    
                    outreach = ""
                    outreach_msgs = res.get("outreach_messages") or []
                    matching_outreach = []
                    for m in outreach_msgs:
                        m_prod = getattr(m, "product_type", None) or getattr(m, "product", None)
                        if m_prod == opp.product_recommended:
                            matching_outreach.append(m)
                    if matching_outreach:
                        outreach = getattr(matching_outreach[0], "message_body", None) or getattr(matching_outreach[0], "body", "")
                    
                    opportunities_found.append({
                        "customer_name": cust.name,
                        "persona": cust.persona_type.value.replace("_", " ").title(),
                        "credit_score": res.get("risk_assessment").credit_score if res.get("risk_assessment") else "N/A",
                        "opportunity": opp,
                        "outreach": outreach
                    })

            opportunities_found.sort(key=lambda o: o["opportunity"].priority_score, reverse=True)

            # 6. Format final response
            response_text = "### 📋 Priority Scoring Pipeline Results\n\n"
            
            # Show active filters in response header
            filters_applied = []
            if parsed_filters.get("persona_type"):
                filters_applied.append(f"Persona: **{parsed_filters['persona_type'].replace('_', ' ').title()}**")
            if parsed_filters.get("event_type"):
                filters_applied.append(f"Event: **{parsed_filters['event_type'].replace('_', ' ').title()}**")
            if parsed_filters.get("product_type"):
                filters_applied.append(f"Product: **{parsed_filters['product_type'].replace('_', ' ').title()}**")
            if parsed_filters.get("time_window"):
                filters_applied.append(f"Time Window: **{parsed_filters['time_window']}**")
                
            if filters_applied:
                response_text += "*Active Filters:* " + " | ".join(filters_applied) + "\n\n"

            # 1. Executive Summary
            response_text += "### 📋 Executive Summary\n"
            if not opportunities_found:
                response_text += "Ran the customer scoring pipeline for the portfolio. Identified **0** customers matching the selected criteria.\n\n"
            else:
                cust_names = [o["customer_name"] for o in opportunities_found]
                response_text += f"Successfully ran the multi-agent scoring graph for your portfolio. Identified **{len(opportunities_found)}** matching opportunities: {', '.join(cust_names)}.\n\n"

            # 2. Key Findings
            response_text += "### 🔍 Key Findings\n"
            if not opportunities_found:
                response_text += "- No high-potential opportunities detected matching the constraints.\n\n"
            else:
                response_text += f"- Total prospects found: **{len(opportunities_found)}**\n"
                high_conv = [o for o in opportunities_found if o["opportunity"].conversion_probability >= 0.8]
                if high_conv:
                    response_text += f"- **{len(high_conv)}** prospects show conversion probabilities above 80%.\n"
                else:
                    response_text += "- No prospects exceed the 80% conversion probability threshold.\n"
                response_text += "\n"

            # 3. Customer Insights
            response_text += "### 👤 Customer Insights\n"
            if not opportunities_found:
                response_text += "No customers met the scoring thresholds.\n\n"
            else:
                for idx, opp_data in enumerate(opportunities_found, 1):
                    opp = opp_data["opportunity"]
                    
                    risk_desc = "None"
                    if opp.risk_flags and opp.risk_flags.get("reason"):
                        risk_desc = opp.risk_flags.get("reason")
                    elif opp.risk_flags and opp.risk_flags.get("flag"):
                        risk_desc = f"Flagged as {opp.risk_flags.get('flag')}"
                    
                    response_text += (
                        f"{idx}. **{opp_data['customer_name']}** ({opp_data['persona']}, CIBIL: {opp_data['credit_score']})\n"
                        f"  - **Recommended Product:** {opp.product_recommended.value.replace('_', ' ').title()}\n"
                        f"  - **Conversion Probability:** {round(opp.conversion_probability * 100)}%\n"
                        f"  - **Priority Score:** {opp.priority_score:.1f}\n"
                        f"  - **Risk Flags:** {risk_desc}\n"
                    )
                    if opp_data["outreach"]:
                        response_text += f"  - **Suggested Outreach:**\n    > *\"{opp_data['outreach']}\"*\n"
                    response_text += "\n"

            # 4. Risk Flags
            response_text += "### ⚠️ Risk Flags\n"
            has_risk = False
            for opp_data in opportunities_found:
                opp = opp_data["opportunity"]
                if opp.risk_flags and opp.risk_flags.get("flag") and opp.risk_flags.get("flag") != "GREEN":
                    has_risk = True
                    response_text += f"- **{opp_data['customer_name']}**: Risk Flag is **{opp.risk_flags.get('flag')}** ({opp.risk_flags.get('reason', 'No reason specified')}).\n"
            if not has_risk:
                response_text += "- No significant risk flags detected for the scored portfolio. All prospects show strong credit indicators.\n\n"
            else:
                response_text += "\n"

            # 5. Recommended Actions
            response_text += "### ⚡ Recommended Actions\n"
            if not opportunities_found:
                response_text += "- Monitor client transactions for future trigger signals.\n\n"
            else:
                for opp_data in opportunities_found:
                    opp = opp_data["opportunity"]
                    response_text += f"- **{opp_data['customer_name']}**: Initiate contact to discuss the {opp.product_recommended.value.replace('_', ' ').title()} offer. Emphasize pre-approved status.\n"
                response_text += "\n"

            # 6. Suggested Outreach
            response_text += "### 💬 Suggested Outreach\n"
            if not opportunities_found:
                response_text += "- No outreach templates generated.\n\n"
            else:
                for opp_data in opportunities_found:
                    if opp_data["outreach"]:
                        response_text += f"- **{opp_data['customer_name']}** (WhatsApp):\n  > *\"{opp_data['outreach']}\"*\n"
                response_text += "\n"

            if all_errors:
                response_text += "\n⚠️ **System warnings/errors encountered during run:**\n"
                for err in all_errors[:3]: # limit display
                    response_text += f"- {err}\n"

            # Stream response to token_queue
            words = response_text.split(" ")
            chunks = []
            for i, word in enumerate(words):
                chunk = word + (" " if i < len(words) - 1 else "")
                chunks.append(chunk)
                if token_queue:
                    await token_queue.put(chunk)
                    await asyncio.sleep(0.01)

            # Return state update
            return {
                "copilot_response_chunks": chunks,
                "agent_trace": accumulated_trace,
                "errors": all_errors
            }

        # --- Standard conversational RAG path ---
        # 1. Retrieve relevant context from all RAG collections
        rag_context = await self._retrieve_rag_context(rm_question)

        # 2. Fetch detailed portfolio context
        portfolio_summary = await self._get_detailed_portfolio_context(rm_id)

        # 3. Build masked prompt
        prompt = render_prompt(
            PromptKey.RM_COPILOT_CONVERSATION,
            rm_name="Relationship Manager",
            rm_question=rm_question,
            portfolio_summary=portfolio_summary,
            rag_context=rag_context,
            current_date=date.today().isoformat(),
        )

        self.assert_no_pii_in_prompt(prompt)

        # 4. Stream response
        chunks: list[str] = []
        try:
            async for token, is_final in get_llm_router().stream_primary(
                prompt=prompt,
                session_id=session_id,
            ):
                if token:
                    chunks.append(token)
                    if token_queue:
                        await token_queue.put(token)
                if is_final:
                    break
        except Exception as exc:
            logger.error("copilot_stream_error", error=str(exc), session_id=session_id)
            err_msg = f"I encountered an error processing your question. Please try again. ({type(exc).__name__})"
            chunks = [err_msg]
            if token_queue:
                await token_queue.put(err_msg)

        logger.info(
            "copilot_response_complete",
            session_id=session_id,
            response_chunks=len(chunks),
        )

        return {"copilot_response_chunks": chunks}

    async def _retrieve_rag_context(self, question: str) -> str:
        """Retrieve relevant context from all four RAG collections in parallel."""
        import asyncio

        async def _fetch_one(collection: str) -> str:
            try:
                from services.orchestrator.tools.vector_tools import hybrid_search
                results = await hybrid_search(
                    query=question,
                    collection=collection,
                    top_k=2,
                    db=self._db,
                    redis_client=self._redis,
                )
                return "\n".join(r.get("content", "")[:300] for r in results)
            except Exception as exc:
                logger.warning("copilot_rag_failed", collection=collection, error=str(exc))
                return ""

        results = []
        for col in _RAG_COLLECTIONS:
            results.append(await _fetch_one(col))
        combined = "\n\n".join(
            f"[{col.upper()}]\n{text}"
            for col, text in zip(_RAG_COLLECTIONS, results)
            if text.strip()
        )
        return combined or "No relevant context found in knowledge base."

    async def _get_detailed_portfolio_context(self, rm_id: str) -> str:
        """Fetch a detailed portfolio context for the RM from the database to inject into standard conversation prompt."""
        if not self._db or not rm_id:
            return "Portfolio summary unavailable."

        try:
            import uuid as py_uuid
            rm_uuid = py_uuid.UUID(rm_id) if isinstance(rm_id, str) else rm_id

            from sqlalchemy.orm import selectinload

            stmt = (
                select(Customer)
                .where(Customer.rm_id == rm_uuid, Customer.deleted_at.is_(None))
                .options(
                    selectinload(Customer.profile),
                    selectinload(Customer.opportunities),
                    selectinload(Customer.detected_events)
                )
            )
            cust_res = await self._db.execute(stmt)
            customers = cust_res.scalars().all()

            portfolio_lines = []
            portfolio_lines.append(f"Total Portfolio Customers: {len(customers)}")
            
            for c in customers:
                prof = c.profile
                opps = [o for o in c.opportunities if o.deleted_at is None]
                events = [e for e in c.detected_events if e.actioned is False]
                
                c_line = (
                    f"- Customer Name: {c.name}\n"
                    f"  Persona: {c.persona_type.value if c.persona_type else 'unknown'}\n"
                    f"  Risk Tier: {c.risk_tier.value if c.risk_tier else 'LOW'}\n"
                    f"  Relationship Tenure: {c.relationship_tenure_months} months\n"
                )
                if prof:
                    salary_val = float(prof.salary_avg_3m) if prof.salary_avg_3m else 0.0
                    bal_val = float(prof.avg_balance_3m) if prof.avg_balance_3m else 0.0
                    inv_val = float(prof.total_investments) if prof.total_investments else 0.0
                    lia_val = float(prof.total_liabilities) if prof.total_liabilities else 0.0
                    
                    c_line += (
                        f"  CIBIL Credit Score: {prof.credit_score if prof.credit_score else 'N/A'}\n"
                        f"  Average Monthly Salary: ₹{salary_val:.2f}\n"
                        f"  Average Balance (3M): ₹{bal_val:.2f}\n"
                        f"  Total Investments: ₹{inv_val:.2f}\n"
                        f"  Total Liabilities: ₹{lia_val:.2f}\n"
                        f"  Products Held: {', '.join(k for k, v in prof.product_holdings.items() if v) if prof.product_holdings else 'None'}\n"
                        f"  Behavioral Tags: {', '.join(prof.behavioral_tags) if prof.behavioral_tags else 'None'}\n"
                    )
                if opps:
                    c_line += "  Active Opportunities:\n"
                    for o in opps:
                        rev_val = float(o.revenue_potential) if o.revenue_potential else 0.0
                        c_line += (
                            f"    * Product: {o.product_recommended.value} | "
                            f"Priority Score: {o.priority_score:.1f} | "
                            f"Conversion Prob: {o.conversion_prob*100:.0f}% | "
                            f"Revenue Potential: ₹{rev_val:.2f} | "
                            f"Risk Flags: {o.risk_flags}\n"
                        )
                if events:
                    c_line += "  Detected Events (Life Signals):\n"
                    for e in events:
                        c_line += (
                            f"    * Event Type: {e.event_type.value} | "
                            f"Confidence: {e.confidence_score*100:.0f}% | "
                            f"Evidence Signals: {e.signals}\n"
                        )
                portfolio_lines.append(c_line)
                
            return "\n".join(portfolio_lines)
        except Exception as exc:
            logger.warning("detailed_portfolio_context_failed", error=str(exc))
            return "Portfolio summary temporarily unavailable."
