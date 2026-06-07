# RM Copilot — Product Demonstration Script

This document details the step-by-step walkthrough script for demonstrating the core capabilities of the RM Copilot platform.

---

## Prerequisites & Setup

Ensure the system is initialized and running before starting the demo:

1. **Database Seeded**: Run `poetry run python scripts/seed_db.py` to populate relationship managers, 20 customers, transaction history, and run event scans.
2. **Backend Gateway**: Run `poetry run uvicorn services.gateway.main:app --reload --port 8000`.
3. **Frontend Dashboard**: Under `frontend/`, run `npm run dev`.

---

## Walkthrough Scenario 1 — Secure Login & Morning Digest

**Objective**: Show secure authentication, custom light/dark styling, and the Morning Digest overview.

1. **Access Dashboard**: Open `http://localhost:5173` in your browser. You will be greeted by the premium, glassmorphic login interface.
2. **Toggle Theme**: Click the sun/moon icon at the bottom of the login card or sidebar to show smooth transition animations between light and dark visual themes.
3. **Log In**: Use the relationship manager credentials:
   - Email: `priya@bank.com`
   - Password: `password123`
   - Click **Secure Login**.
4. **Morning Digest**: 
   - Observe the top sliding accordion widget. It summarizes Priya's portfolio: **10 Active Customers**, **High Risk Alerts**, and **Average CIBIL score**.
   - Click the header to collapse/expand the card with a smooth sliding micro-animation.

---

## Walkthrough Scenario 2 — Priority Queue & Event Insights

**Objective**: Demonstrate how deterministic transaction event scans feed the priority pipeline.

1. **Scan Priority Cards**: Under the **Priority Queue**, view the list of Priya's customers. Note the risk badges (Low, Medium, High Risk) and behavioural tags (e.g., "investor", "travel_heavy").
2. **Select Customer**: Click on **Ishaan Verma** (Newly Married persona, high priority).
3. **Review Financial Diagnostics**:
   - The middle details panel updates with a bouncy slide-in animation.
   - Observe Ishaan's monthly salary (₹110,000), total investments (₹500,000), liabilities, and complete KYC status.
4. **Trigger Scan**: Click **Trigger Scan** in the header to show the refresh micro-animation, representing the automated execution of the LangGraph customer scoring pipelines in the background.

---

## Walkthrough Scenario 3 — AI Explanation Card

**Objective**: Show compliance-ready, plain-English explanation models for product recommendations.

1. **Select Opportunity**: In Ishaan's active opportunity section, note the recommendation for a **Personal Loan** with an estimated conversion probability of **78%** and ₹160,000 revenue potential.
2. **Click Explain Card**: Click the **Explain Card** button. This invokes the explainability agent (representing gpt-4o).
3. **Analyze Diagnostics**: A glassmorphic modal overlay appears:
   - **Why Selected**: Highlights transaction spikes matching wedding bookings.
   - **Event Significance**: Detailed explanation of detected banquet and jewellery purchases.
   - **Product Rationale**: Validates why a Personal Loan fits this life stage.
   - **RM Action**: Clear guidelines prompting immediate contact.
4. **Close Modal**: Click **Acknowledge** or the close button to close.

---

## Walkthrough Scenario 4 — Compliant Outreach & Guardrails

**Objective**: Verify PII masking, channels selection, and Celery worker dispatch.

1. **Trigger Outreach**: Click **WhatsApp Outreach** on Ishaan's opportunity card.
2. **Review Masked Draft**:
   - Note the editor pre-populates with a message drafted by the `OutreachGenAgent`.
   - Observe the compliance check guardrail: the message is addressed to **"Dear Valued Customer"** (no raw PII names, phone numbers, or account numbers appear in the text).
   - If any PII tokens had leaked from the LLM, they would be automatically caught and replaced with generic placeholders.
3. **Edit Draft**: In the textarea, add a custom note (e.g., *"Looking forward to speaking soon!"*).
4. **Send Approval**: Click **Approve & Dispatch**.
5. **Worker Execution**: Note the success animation. This sends the approved campaign to the Redis-backed Celery worker queue, performing DND checks and Meta WhatsApp limits verification before dispatching.

---

## Walkthrough Scenario 5 — Conversational Copilot & RAG Citations

**Objective**: Demonstrate SSE streaming chat and vector knowledge base citations.

1. **Focus Chat Copilot**: Open the floating chat widget on the bottom-right.
2. **Ask Question**: Type: *"What is the personal loan eligibility criteria for IT professionals?"* and hit Enter.
3. **Observe SSE Streaming**: Note the text streams token-by-token in real time.
4. **Check RAG Citations**:
   - Hover over the custom citation badges at the bottom of the response.
   - Observe the tooltips displaying the source document (`product_catalog/personal_loan_eligibility.md`) and the excerpt text retrieved during hybrid vector search.

---

## Walkthrough Scenario 6 — Interactive Analytics

**Objective**: Inspect the aggregate revenue pipeline.

1. **Open Analytics Hub**: Click **Analytics Hub** on the left sidebar.
2. **Review Charts**:
   - **Priority Pipeline**: Bar chart showing potential revenue by product type.
   - **Risk Segment Distribution**: Donut/pie chart mapping low, medium, and high-risk customers.
   - **Conversion Rates**: Gradients-backed Area chart charting conversion statistics.
3. **Logout**: Click the logout icon in the bottom corner to return to the secure login screen.
