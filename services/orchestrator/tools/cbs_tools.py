# Core Banking System (CBS) integration tools.
# Pulls live transaction data, account balances, and product holdings from CBS via API.
# Circuit breaker: if CBS fails 3 consecutive times, falls back to PostgreSQL snapshot.
# All CBS responses are stripped of raw PII before being written to AgentState.
# customer_token (masked ID) is used in all API calls — never the real account number.

# TODO: implement CBS tools with circuit breaker in Phase 8 (integration layer)
