# LightGBM churn prediction model.
# Predicts P(customer churns within 90 days | current account behaviour).
# Features: avg_balance_trend_3m, transaction_frequency_delta, num_products_held,
#           last_rm_interaction_days, digital_engagement_score, competitor_outflow_flag.
# Output: float in [0, 1] — churn probability within 90 days.
# High churn probability triggers defensive retention outreach rather than new product pitch.

# TODO: implement LightGBM churn model wrapper in Phase 7 (ML layer)
