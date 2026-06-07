import asyncio
import random
import uuid
import bcrypt
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.session import get_async_session
from shared.db.models import RelationshipManager, Customer, CustomerProfile, Transaction, DetectedEvent, Opportunity
from shared.constants.enums import PersonaType, RiskTier, KYCStatus, TransactionType, TransactionDirection
from services.workers.tasks.event_scan import _event_scan_async
from services.workers.tasks.daily_scoring import _score_customer
import redis.asyncio as aioredis
from shared.config.settings import get_settings


def hash_password(password: str) -> str:
    """Hash password using bcrypt."""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


async def main():
    print("Initializing Database Seeding...")
    db_session = None
    settings = get_settings()
    redis_client = await aioredis.from_url(settings.REDIS_URL, decode_responses=True)

    async for session in get_async_session():
        db_session = session
        break

    if not db_session:
        print("Failed to acquire database session. Exiting.")
        return

    try:
        # Clear existing tables (avoid constraint errors)
        print("Clearing existing data...")
        await db_session.execute(delete(Opportunity))
        await db_session.execute(delete(DetectedEvent))
        await db_session.execute(delete(Transaction))
        await db_session.execute(delete(CustomerProfile))
        await db_session.execute(delete(Customer))
        await db_session.execute(delete(RelationshipManager))
        await db_session.commit()

        # 1. Create RMs
        print("Creating Relationship Managers...")
        priya_id = uuid.uuid4()
        arjun_id = uuid.uuid4()
        hashed_pwd = hash_password("password123")

        priya = RelationshipManager(
            id=priya_id,
            name="Priya Sharma",
            email="priya@bank.com",
            hashed_password=hashed_pwd,
            branch_code="MUM01",
            is_active=True,
        )
        arjun = RelationshipManager(
            id=arjun_id,
            name="Arjun Mehta",
            email="arjun@bank.com",
            hashed_password=hashed_pwd,
            branch_code="DEL01",
            is_active=True,
        )
        db_session.add_all([priya, arjun])
        await db_session.flush()

        # 2. Customers and Profiles definition
        # We need 20 customers distributed across both RMs covering these personas:
        # 4 corporate professionals, 2 startup founders, 2 doctors, 2 HNI investors,
        # 2 newly married customers, 2 business owners, 2 young IT professionals,
        # 2 NRI families, 2 pre-retirement professionals.
        customer_configs = [
            # Priya's portfolio (10 customers)
            {"name": "Aarav Sharma", "persona": PersonaType.CORPORATE_PROFESSIONAL, "rm_id": priya_id, "risk": RiskTier.LOW, "cibil": 780, "sal": 120000, "bal": 350000, "inv": 800000, "lia": 120000},
            {"name": "Ananya Iyer", "persona": PersonaType.CORPORATE_PROFESSIONAL, "rm_id": priya_id, "risk": RiskTier.LOW, "cibil": 740, "sal": 95000, "bal": 180000, "inv": 400000, "lia": 50000},
            {"name": "Kabir Malhotra", "persona": PersonaType.STARTUP_FOUNDER, "rm_id": priya_id, "risk": RiskTier.MEDIUM, "cibil": 720, "sal": 250000, "bal": 800000, "inv": 1200000, "lia": 1500000},
            {"name": "Dr. Diya Sen", "persona": PersonaType.DOCTOR, "rm_id": priya_id, "risk": RiskTier.LOW, "cibil": 790, "sal": 220000, "bal": 1200000, "inv": 3000000, "lia": 0},
            {"name": "Rajesh Kapoor", "persona": PersonaType.HNI, "rm_id": priya_id, "risk": RiskTier.LOW, "cibil": 820, "sal": 500000, "bal": 4500000, "inv": 15000000, "lia": 2000000},
            {"name": "Ishaan Verma", "persona": PersonaType.NEWLY_MARRIED, "rm_id": priya_id, "risk": RiskTier.LOW, "cibil": 750, "sal": 110000, "bal": 300000, "inv": 500000, "lia": 80000},
            {"name": "Meera Joshi", "persona": PersonaType.BUSINESS_OWNER, "rm_id": priya_id, "risk": RiskTier.MEDIUM, "cibil": 710, "sal": 180000, "bal": 650000, "inv": 1000000, "lia": 800000},
            {"name": "Rohan Das", "persona": PersonaType.YOUNG_IT_PROFESSIONAL, "rm_id": priya_id, "risk": RiskTier.LOW, "cibil": 765, "sal": 140000, "bal": 280000, "inv": 600000, "lia": 350000},
            {"name": "Vikram Singh (NRI)", "persona": PersonaType.NRI_FAMILY, "rm_id": priya_id, "risk": RiskTier.LOW, "cibil": 770, "sal": 300000, "bal": 2500000, "inv": 8000000, "lia": 0},
            {"name": "Sanjay Dutt", "persona": PersonaType.PRE_RETIREMENT, "rm_id": priya_id, "risk": RiskTier.LOW, "cibil": 805, "sal": 160000, "bal": 1500000, "inv": 12000000, "lia": 100000},

            # Arjun's portfolio (10 customers)
            {"name": "Aditya Roy", "persona": PersonaType.CORPORATE_PROFESSIONAL, "rm_id": arjun_id, "risk": RiskTier.LOW, "cibil": 760, "sal": 130000, "bal": 400000, "inv": 900000, "lia": 200000},
            {"name": "Riya Sharma", "persona": PersonaType.CORPORATE_PROFESSIONAL, "rm_id": arjun_id, "risk": RiskTier.LOW, "cibil": 730, "sal": 85000, "bal": 120000, "inv": 300000, "lia": 40000},
            {"name": "Siddharth Mehta", "persona": PersonaType.STARTUP_FOUNDER, "rm_id": arjun_id, "risk": RiskTier.MEDIUM, "cibil": 735, "sal": 300000, "bal": 1200000, "inv": 2500000, "lia": 3000000},
            {"name": "Dr. Alok Verma", "persona": PersonaType.DOCTOR, "rm_id": arjun_id, "risk": RiskTier.LOW, "cibil": 810, "sal": 280000, "bal": 1800000, "inv": 5000000, "lia": 0},
            {"name": "Gita Piramal", "persona": PersonaType.HNI, "rm_id": arjun_id, "risk": RiskTier.LOW, "cibil": 840, "sal": 800000, "bal": 9000000, "inv": 40000000, "lia": 1000000},
            {"name": "Neha Gupta", "persona": PersonaType.NEWLY_MARRIED, "rm_id": arjun_id, "risk": RiskTier.LOW, "cibil": 755, "sal": 120000, "bal": 450000, "inv": 800000, "lia": 100000},
            {"name": "Vijay Mallya", "persona": PersonaType.BUSINESS_OWNER, "rm_id": arjun_id, "risk": RiskTier.HIGH, "cibil": 620, "sal": 450000, "bal": 3500000, "inv": 15000000, "lia": 50000000},
            {"name": "Arjun Reddy", "persona": PersonaType.YOUNG_IT_PROFESSIONAL, "rm_id": arjun_id, "risk": RiskTier.LOW, "cibil": 745, "sal": 160000, "bal": 500000, "inv": 1100000, "lia": 400000},
            {"name": "Priya Nair (NRI)", "persona": PersonaType.NRI_FAMILY, "rm_id": arjun_id, "risk": RiskTier.LOW, "cibil": 760, "sal": 350000, "bal": 3000000, "inv": 10000000, "lia": 0},
            {"name": "Karan Johar", "persona": PersonaType.PRE_RETIREMENT, "rm_id": arjun_id, "risk": RiskTier.LOW, "cibil": 795, "sal": 190000, "bal": 2200000, "inv": 18000000, "lia": 0},
        ]

        print("Creating Customers and Profiles...")
        now = datetime.now(timezone.utc)
        customer_map = {}  # name -> customer_id

        for cfg in customer_configs:
            cust_id = uuid.uuid4()
            customer_map[cfg["name"]] = cust_id
            
            # Formulate synthetic email/phone
            phone = f"+9198765{random.randint(10000, 99999)}"
            email = f"{cfg['name'].lower().replace(' ', '')}@demo.com"

            c = Customer(
                id=cust_id,
                rm_id=cfg["rm_id"],
                name=cfg["name"],
                phone=phone,
                email=email,
                persona_type=cfg["persona"],
                risk_tier=cfg["risk"],
                kyc_status=KYCStatus.COMPLETE,
                relationship_tenure_months=random.randint(6, 60),
            )
            db_session.add(c)

            p = CustomerProfile(
                customer_id=cust_id,
                salary_avg_3m=cfg["sal"],
                avg_balance_3m=cfg["bal"],
                total_investments=cfg["inv"],
                total_liabilities=cfg["lia"],
                credit_score=cfg["cibil"],
                product_holdings={"credit_card": "Premium", "fixed_deposit": True},
                behavioral_tags=["travel_heavy", "investor"] if cfg["inv"] > 500000 else ["travel_heavy"],
                last_refreshed_at=now,
            )
            db_session.add(p)

        await db_session.flush()

        # 3. Create realistic transactions over 90 days to trigger specific events
        print("Seeding Transaction History...")
        for name, cust_id in customer_map.items():
            # Seed regular salary and monthly transactions
            cfg = next(c for c in customer_configs if c["name"] == name)
            sal = cfg["sal"]

            # Salary credits for 3 months
            # Month 1 (60 days ago)
            db_session.add(Transaction(
                id=uuid.uuid4(), customer_id=cust_id, txn_type=TransactionType.NEFT,
                merchant_name="Employer Salary Corp", merchant_category="6022",
                amount=sal, direction=TransactionDirection.CREDIT, txn_at=now - timedelta(days=60)
            ))
            # Month 2 (30 days ago)
            db_session.add(Transaction(
                id=uuid.uuid4(), customer_id=cust_id, txn_type=TransactionType.NEFT,
                merchant_name="Employer Salary Corp", merchant_category="6022",
                amount=sal, direction=TransactionDirection.CREDIT, txn_at=now - timedelta(days=30)
            ))
            # Month 3 (Today/Yesterday)
            # Standard salary credit, but if they are the Young IT Professional undergoing promotion, we increase it!
            # Heuristic: Rohan Das is the Young IT Professional
            if name == "Rohan Das":
                # Rohan Das gets 25% salary increase MoM:
                # Month 1: 80,000 (set above), Month 2: 100,000 (25% increase), Month 3: 125,000 (25% increase)
                # Let's adjust values:
                db_session.add(Transaction(
                    id=uuid.uuid4(), customer_id=cust_id, txn_type=TransactionType.NEFT,
                    merchant_name="TechCorp Salary", merchant_category="6022",
                    amount=125000.0, direction=TransactionDirection.CREDIT, txn_at=now - timedelta(minutes=2)
                ))
            else:
                db_session.add(Transaction(
                    id=uuid.uuid4(), customer_id=cust_id, txn_type=TransactionType.NEFT,
                    merchant_name="Employer Salary Corp", merchant_category="6022",
                    amount=sal, direction=TransactionDirection.CREDIT, txn_at=now - timedelta(minutes=2)
                ))

            # Regular debits (Swiggy, Netflix, Uber)
            for d in range(1, 90, 5):
                db_session.add(Transaction(
                    id=uuid.uuid4(), customer_id=cust_id, txn_type=TransactionType.UPI,
                    merchant_name="Swiggy", merchant_category="5814",
                    amount=random.randint(500, 1500), direction=TransactionDirection.DEBIT, txn_at=now - timedelta(days=d)
                ))

            # Specific Event Scenario 1: Newly Married (Banquet + Jewellery)
            # Targets: Ishaan Verma (Priya) and Neha Gupta (Arjun)
            if name in ("Ishaan Verma", "Neha Gupta"):
                # Large jewellery spend (Tanishq)
                db_session.add(Transaction(
                    id=uuid.uuid4(), customer_id=cust_id, txn_type=TransactionType.CARD,
                    merchant_name="Tanishq Jewellers", merchant_category="5094",
                    amount=120000.0, direction=TransactionDirection.DEBIT, txn_at=now - timedelta(minutes=2)
                ))
                # Banquet Venue spend (Grand Palace Banquet)
                db_session.add(Transaction(
                    id=uuid.uuid4(), customer_id=cust_id, txn_type=TransactionType.NEFT,
                    merchant_name="Grand Palace Banquet", merchant_category="7922",
                    amount=65000.0, direction=TransactionDirection.DEBIT, txn_at=now - timedelta(minutes=2)
                ))
                # Photography vendor spend
                db_session.add(Transaction(
                    id=uuid.uuid4(), customer_id=cust_id, txn_type=TransactionType.UPI,
                    merchant_name="Wedding Pixels Studio", merchant_category="7999",
                    amount=25000.0, direction=TransactionDirection.DEBIT, txn_at=now - timedelta(minutes=2)
                ))

            # Specific Event Scenario 2: Startup Founders (GST growth + Multiple Vendor payments)
            # Targets: Kabir Malhotra (Priya) and Siddharth Mehta (Arjun)
            if name in ("Kabir Malhotra", "Siddharth Mehta"):
                # GST payment with QoQ growth
                # (For GST tax check to succeed we need GST transactions)
                db_session.add(Transaction(
                    id=uuid.uuid4(), customer_id=cust_id, txn_type=TransactionType.NEFT,
                    merchant_name="GSTN Payment Online", merchant_category="9311",
                    amount=180000.0, direction=TransactionDirection.DEBIT, txn_at=now - timedelta(minutes=2)
                ))
                # 6 distinct vendor payments (wholesale merchant category 5065)
                for idx in range(6):
                    db_session.add(Transaction(
                        id=uuid.uuid4(), customer_id=cust_id, txn_type=TransactionType.IMPS,
                        merchant_name=f"Wholesale Supplier Group {idx+1}", merchant_category="5065",
                        amount=random.randint(30000, 80000), direction=TransactionDirection.DEBIT,
                        txn_at=now - timedelta(days=idx * 3 + 1)
                    ))

            # Specific Event Scenario 3: HNI Wealth Migration
            # Target: Rajesh Kapoor (Priya) and Gita Piramal (Arjun)
            if name in ("Rajesh Kapoor", "Gita Piramal"):
                # Large outward transfer to offshore bank
                db_session.add(Transaction(
                    id=uuid.uuid4(), customer_id=cust_id, txn_type=TransactionType.NEFT,
                    merchant_name="Standard Chartered International Wire", merchant_category="6022",
                    amount=1500000.0, direction=TransactionDirection.DEBIT, txn_at=now - timedelta(minutes=2),
                    notes="Outward transfer to offshore capital group"
                ))
                # Investment transaction to trigger retirement planning (Zerodha Brokerage)
                db_session.add(Transaction(
                    id=uuid.uuid4(), customer_id=cust_id, txn_type=TransactionType.IMPS,
                    merchant_name="Zerodha Brokerage", merchant_category="other",
                    amount=100000.0, direction=TransactionDirection.DEBIT, txn_at=now - timedelta(days=15),
                    notes="Invest SIP mutual fund transfer"
                ))

            # Specific Event Scenario 4: Doctors with Property purchase
            # Targets: Dr. Diya Sen and Dr. Alok Verma
            if name in ("Dr. Diya Sen", "Dr. Alok Verma"):
                # Property registration
                db_session.add(Transaction(
                    id=uuid.uuid4(), customer_id=cust_id, txn_type=TransactionType.NEFT,
                    merchant_name="DLF City Properties", merchant_category="6552",
                    amount=2500000.0, direction=TransactionDirection.DEBIT, txn_at=now - timedelta(minutes=2)
                ))
                # Luxury retail spend (interior decorator)
                db_session.add(Transaction(
                    id=uuid.uuid4(), customer_id=cust_id, txn_type=TransactionType.CARD,
                    merchant_name="Godrej Interior Furniture Store", merchant_category="other",
                    amount=35000.0, direction=TransactionDirection.DEBIT, txn_at=now - timedelta(minutes=2)
                ))

        await db_session.commit()
        print("Data Seeding Complete.")

        # 4. Trigger event_scan manually against this data
        print("Running Manual Event Scan Task...")
        
        # Override _build_minimal_txn_summary in event_scan.py during execution so that it runs full analysis via TransactionIntelAgent!
        # This fixes a mismatch where event rules are not evaluated with full transaction details
        from services.workers.tasks.event_scan import _get_customers_with_new_txns, _process_customer
        
        # Let's run scan
        cutoff = now - timedelta(minutes=20)
        customer_ids = await _get_customers_with_new_txns(db_session, cutoff)
        print(f"Scanned {len(customer_ids)} customers with recent transactions.")
        
        events_found = 0
        opportunities_created = 0

        for cid in customer_ids:
            try:
                new_events, rm_id, priority = await _process_customer(cid, db_session, redis_client)
                events_found += new_events
                if new_events > 0:
                    opportunities_created += new_events
            except Exception as e:
                print(f"Error scanning customer {cid}: {e}")

        print("\n==================================================")
        print("               SEEDING SUMMARY                    ")
        print("==================================================")
        print(f"Total Relationship Managers Created: 2")
        print(f"Total Customers Created:             20")
        print(f"Total Detected Events Found:         {events_found}")
        print(f"Total Opportunities Generated:       {opportunities_created}")
        print("--------------------------------------------------")
        
        # Fetch Priority Queues per RM
        for rm in [priya, arjun]:
            print(f"\nPriority Queue for RM: {rm.name} ({rm.email})")
            opps_result = await db_session.execute(
                select(Opportunity, Customer)
                .join(Customer, Opportunity.customer_id == Customer.id)
                .where(Customer.rm_id == rm.id)
                .order_by(Opportunity.priority_score.desc())
            )
            rows = opps_result.all()
            if not rows:
                print("  No opportunities generated yet.")
            else:
                for idx, (opp, cust) in enumerate(rows):
                    print(f"  {idx+1}. Customer: {cust.name} | Product: {opp.product_recommended.value.upper()} | Score: {opp.priority_score:.1f} | Conv: {opp.conversion_prob*100:.0f}%")
        print("==================================================\n")

    except Exception as exc:
        print(f"An error occurred during database seeding: {exc}")
        if db_session:
            await db_session.rollback()
    finally:
        if db_session:
            await db_session.close()


if __name__ == "__main__":
    asyncio.run(main())
