# Feature store interface — provides consistent feature access for ML models.
# In production this would wrap a Feast feature store or similar.
# For the demo it reads directly from PostgreSQL and TimescaleDB.
# Ensures: feature consistency between training and inference,
# point-in-time correctness (no data leakage), and feature versioning.

# TODO: implement feature store with point-in-time correct lookups in Phase 7 (ML layer)
