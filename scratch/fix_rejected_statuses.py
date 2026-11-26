import sys
sys.path.insert(0, 'backend')
from app.database import SessionLocal
from sqlalchemy import text

db = SessionLocal()

# Update all jobs with status = 'REJECTED_BY_TECHNICIAN' to 'QUEUED'
result = db.execute(text("""
    UPDATE jobs 
    SET status = 'QUEUED'
    WHERE status = 'REJECTED_BY_TECHNICIAN';
"""))
db.commit()
print(f"Updated {result.rowcount} jobs from REJECTED_BY_TECHNICIAN to QUEUED.")

# Verify distinct statuses
statuses = db.execute(text("SELECT status, count(*) FROM jobs GROUP BY status ORDER BY count(*) DESC")).fetchall()
print("\nDistinct statuses in DB now:")
for s in statuses:
    print(f"  {s[0]}: {s[1]} jobs")

db.close()
