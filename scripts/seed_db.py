# Database seeding script — populates the database with synthetic customer data for local dev.
# Generates: 20 synthetic customers across 4 personas, 90 days of transaction history each,
# pre-seeded events (3 weddings, 2 promotions, 1 business expansion), and scored opportunities.
# Safe to run multiple times — uses upsert logic to avoid duplicates.
# NEVER run against production database.

# TODO: implement seed logic using SQLAlchemy async session in Phase 2 (database layer)

if __name__ == "__main__":
    print("Database seeder — implementation pending Phase 2")
    print("Will seed: 20 synthetic customers, 90-day transaction history, pre-built demo scenarios")
