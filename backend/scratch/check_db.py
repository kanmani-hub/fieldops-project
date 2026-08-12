import sys
import os
from pathlib import Path
from sqlalchemy import text

sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal

def main():
    db = SessionLocal()
    try:
        print("--- Technicians ---")
        tech_query = text("SELECT technician_id, tech_id, technician_name, technician_status, technician_location FROM technicians LIMIT 50")
        techs = db.execute(tech_query).fetchall()
        print(f"Total technicians fetched: {len(techs)}")
        for t in techs:
            print(f"ID={t.technician_id} TechID={t.tech_id} Name={t.technician_name} Status={t.technician_status} Loc={t.technician_location}")
            
        print("\n--- Active Jobs & Assigned Technicians ---")
        job_query = text("SELECT id, customer_name, status, assigned_technician_id, tenant_id FROM jobs WHERE UPPER(status) IN ('ASSIGNED', 'EN_ROUTE', 'ON_SITE')")
        jobs = db.execute(job_query).fetchall()
        print(f"Total active jobs fetched: {len(jobs)}")
        for j in jobs:
            print(f"JobID={j.id} CustomerName={j.customer_name} Status={j.status} AssignedTechID={j.assigned_technician_id}")

        print("\n--- Recent GPS Pings ---")
        ping_query = text("SELECT technician_id, latitude, longitude, timestamp FROM gps_pings ORDER BY timestamp DESC LIMIT 20")
        pings = db.execute(ping_query).fetchall()
        print(f"Total pings: {len(pings)}")
        for p in pings:
            print(f"TechID={p.technician_id} Lat={p.latitude} Lng={p.longitude} Time={p.timestamp}")
    except Exception as e:
        print(f"Error checking DB: {e}")
    finally:
        db.close()

if __name__ == '__main__':
    main()
