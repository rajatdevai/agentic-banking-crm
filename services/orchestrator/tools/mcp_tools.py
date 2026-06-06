# MCP (Market Context Provider) connector tools.
# Fetches live market data used for opportunity timing and messaging context.
# Functions: get_repo_rate(), get_market_index(index), get_realestate_index(city_code).
# All responses are Redis-cached with appropriate TTLs (repo rate: 24h, index: 15min).
# Used by: OpportunityScoringAgent and OutreachGenAgent for market-aware context.

# TODO: implement MCP connector functions with Redis caching in Phase 8 (integration layer)
