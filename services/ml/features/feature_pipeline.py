# Feature engineering pipeline — transforms raw DB data into ML-ready feature vectors.
# Features for conversion_score model:
#   salary_stability_3m, avg_balance_3m, existing_loan_count, cibil_score,
#   event_type_encoded, persona_type_encoded, relationship_tenure_months,
#   foir_current, spend_volatility_30d, num_products_held
# Features are normalised and encoded consistently between training and inference.

# TODO: implement feature pipeline with SQL → feature vector transforms in Phase 7 (ML layer)
