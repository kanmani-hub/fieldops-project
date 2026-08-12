import os
import time
import random
import uuid
from datetime import datetime, timezone
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
from pathlib import Path

# Load environment variables
backend_dir = Path(__file__).resolve().parent.parent
env_path = backend_dir / '.env'
load_dotenv(dotenv_path=env_path)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL not found in environment variables")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def run_simulation():
    db = SessionLocal()
    try:
        # 1. Fetch technicians who are assigned to active jobs
        # Active job statuses are ASSIGNED, EN_ROUTE, ON_SITE
        sql = text("""
            SELECT 
                t.technician_id, 
                t.tech_id, 
                t.tenant_id, 
                t.technician_name,
                j.id AS job_id, 
                j.status AS job_status,
                t.technician_location
            FROM technicians t
            JOIN jobs j ON j.assigned_technician_id = t.technician_id
            WHERE UPPER(j.status) IN ('ASSIGNED', 'EN_ROUTE', 'ON_SITE')
        """)
        
        active_relations = db.execute(sql).fetchall()
        if not active_relations:
            print("No technicians found with active jobs (ASSIGNED/EN_ROUTE/ON_SITE).")
            # Let's activate a couple of jobs/techs to test
            print("Activating a few jobs/techs for simulation...")
            db.execute(text("UPDATE jobs SET status = 'EN_ROUTE' WHERE assigned_technician_id IS NOT NULL LIMIT 3"))
            db.commit()
            active_relations = db.execute(sql).fetchall()
            if not active_relations:
                print("Still no active jobs. Please assign technicians to jobs first.")
                return

        print(f"Simulating GPS pings for {len(active_relations)} active technicians...")
        
        # 2. Insert/Update GPS pings
        for rel in active_relations:
            tech_id = rel.tech_id
            job_id = rel.job_id
            tenant_id = rel.tenant_id
            tech_name = rel.technician_name
            job_status = rel.job_status
            
            # Parse base location or default near Chennai center
            lat, lng = 13.0827, 80.2707
            if rel.technician_location:
                try:
                    parts = rel.technician_location.split(',')
                    lat = float(parts[0])
                    lng = float(parts[1])
                except:
                    pass
            
            # Add small random movement to make them move on the map
            lat_moving = lat + (random.random() - 0.5) * 0.002
            lng_moving = lng + (random.random() - 0.5) * 0.002
            
            # Update technician location field in DB too so initial seed matches
            db.execute(
                text("UPDATE technicians SET technician_location = :loc, last_ping = :now WHERE tech_id = :tech_id"),
                {"loc": f"{lat_moving},{lng_moving}", "now": datetime.now(timezone.utc), "tech_id": tech_id}
            )
            
            # Insert GPS Ping record
            ping_id = str(uuid.uuid4())
            db.execute(
                text("""
                    INSERT INTO gps_pings (id, technician_id, job_id, latitude, longitude, timestamp, accuracy, altitude, tenant_id, created_at)
                    VALUES (:id, :tech_id, :job_id, :lat, :lng, :timestamp, 10.0, 50.0, :tenant_id, :now)
                """),
                {
                    "id": ping_id,
                    "tech_id": tech_id,
                    "job_id": str(job_id),
                    "lat": lat_moving,
                    "lng": lng_moving,
                    "timestamp": datetime.now(timezone.utc),
                    "tenant_id": tenant_id,
                    "now": datetime.now(timezone.utc)
                }
            )
            print(f"Sent GPS Ping for {tech_name} ({job_status}) at {lat_moving:.6f}, {lng_moving:.6f}")
        
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"Error in simulation: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    print("Starting Live GPS Ping simulation...")
    while True:
        try:
            run_simulation()
        except KeyboardInterrupt:
            print("Stopping simulation.")
            break
        except Exception as e:
            print(f"Loop error: {e}")
        time.sleep(5)
