import os
import uuid
import datetime
import random
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
from pathlib import Path

# Load environment variables
env_path = Path(__file__).resolve().parent / '.env'
load_dotenv(dotenv_path=env_path)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL not found in environment variables")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def seed_data():
    from app.models import (
        Technician, Job, AuditEvent, InAppNotification,
        DispatcherNotification, SLAEscalation, DispatcherAlert,
        OverrideAuditEvent, AssignmentOverride, GPSPing
    )
    db = SessionLocal()
    try:
        print("Cleaning existing database records...")
        db.query(AuditEvent).delete()
        db.query(InAppNotification).delete()
        db.query(DispatcherNotification).delete()
        db.query(SLAEscalation).delete()
        db.query(DispatcherAlert).delete()
        db.query(OverrideAuditEvent).delete()
        db.query(AssignmentOverride).delete()
        db.query(GPSPing).delete()
        # Set assigned technician to None first to avoid FK constraints
        db.query(Job).update({Job.assigned_technician_id: None})
        db.commit()
        db.query(Job).delete()
        db.query(Technician).delete()
        db.commit()

        print("Seeding technicians...")
        techs_data = [
            # 5 Available
            {"name": "Rajesh Kumar", "skill": "HVAC", "status": "Available", "current": 0},
            {"name": "Suresh Nair", "skill": "Electrical", "status": "Available", "current": 0},
            {"name": "Amit Patel", "skill": "Plumbing", "status": "Available", "current": 0},
            {"name": "Karthik Raja", "skill": "Mechanical", "status": "Available", "current": 0},
            {"name": "Arun Prasath", "skill": "Other", "status": "Available", "current": 0},
            
            # 8 Busy / On Job
            {"name": "Vijay Iyer", "skill": "HVAC", "status": "Busy", "current": 1},
            {"name": "Sanjay Dutt", "skill": "HVAC", "status": "Busy", "current": 1},
            {"name": "Manoj Bajpayee", "skill": "HVAC", "status": "Busy", "current": 1},
            {"name": "Vikram Singh", "skill": "Electrical", "status": "Busy", "current": 1},
            {"name": "Rohan Mehta", "skill": "Electrical", "status": "Busy", "current": 1},
            {"name": "Deepak Gupta", "skill": "Plumbing", "status": "Busy", "current": 1},
            {"name": "Aditya Roy", "skill": "Mechanical", "status": "Busy", "current": 1},
            {"name": "Rahul Dravid", "skill": "Other", "status": "Busy", "current": 1},
            
            # 2 Break
            {"name": "Preeti Shenoy", "skill": "HVAC", "status": "Break", "current": 0},
            {"name": "Sunita Rao", "skill": "Electrical", "status": "Break", "current": 0},
            
            # 3 Offline
            {"name": "Anjali Desai", "skill": "HVAC", "status": "Offline", "current": 0},
            {"name": "Neha Sharma", "skill": "Plumbing", "status": "Offline", "current": 0},
            {"name": "Vijay Sethupathi", "skill": "Mechanical", "status": "Offline", "current": 0},
        ]

        inserted_techs = []
        for i, t_info in enumerate(techs_data):
            # Coordinates near Chennai center
            lat_offset = (random.random() - 0.5) * 0.08
            lng_offset = (random.random() - 0.5) * 0.08
            tech = Technician(
                technician_name=t_info["name"],
                technician_skill=t_info["skill"],
                technician_location=f"{13.0827 + lat_offset},{80.2707 + lng_offset}",
                technician_status=t_info["status"],
                current_jobs=t_info["current"],
                max_jobs=5,
                tech_id=str(uuid.uuid4()),
                tenant_id="tenant-1",
                last_ping=datetime.datetime.now(datetime.timezone.utc)
            )
            db.add(tech)
            db.commit()
            db.refresh(tech)
            inserted_techs.append(tech)

        print(f"Seeded {len(inserted_techs)} technicians.")

        # Get the busy technicians to assign active jobs to them
        busy_techs = [t for t in inserted_techs if t.technician_status == "Busy"]

        print("Seeding jobs...")
        # 10 Completed, 5 En Route, 8 Assigned, 1 Pending
        # Split categories: 9 HVAC, 6 Electrical, 4 Plumbing, 3 Mechanical, 2 Other
        jobs_to_seed = [
            # HVAC (9 total)
            # 4 Completed
            {"skill": "HVAC", "type": "HVAC Repair", "status": "completed", "assigned": False},
            {"skill": "HVAC", "type": "HVAC Maintenance", "status": "completed", "assigned": False},
            {"skill": "HVAC", "type": "AC Service", "status": "completed", "assigned": False},
            {"skill": "HVAC", "type": "Cooling System Fixing", "status": "completed", "assigned": False},
            # 2 En Route (in progress replacement)
            {"skill": "HVAC", "type": "HVAC Filter Change", "status": "en_route", "assigned": True},
            {"skill": "HVAC", "type": "Thermostat Installation", "status": "en_route", "assigned": True},
            # 3 Assigned (active replacement)
            {"skill": "HVAC", "type": "Compressor Replacement", "status": "assigned", "assigned": True},
            {"skill": "HVAC", "type": "Condenser Fan Fixing", "status": "assigned", "assigned": True},
            {"skill": "HVAC", "type": "AC Gas Charging", "status": "assigned", "assigned": True},

            # Electrical (6 total)
            # 2 Completed
            {"skill": "Electrical", "type": "Wiring Repair", "status": "completed", "assigned": False},
            {"skill": "Electrical", "type": "Switchboard Fixing", "status": "completed", "assigned": False},
            # 1 En Route
            {"skill": "Electrical", "type": "Lighting Installation", "status": "en_route", "assigned": True},
            # 2 Assigned
            {"skill": "Electrical", "type": "Short Circuit Troubleshooting", "status": "assigned", "assigned": True},
            {"skill": "Electrical", "type": "Generator Service", "status": "assigned", "assigned": True},
            # 1 Unassigned Pending
            {"skill": "Electrical", "type": "CCTV Camera Electrical Line", "status": "assigned", "assigned": False},

            # Plumbing (4 total)
            # 2 Completed
            {"skill": "Plumbing", "type": "Pipe Leak Repair", "status": "completed", "assigned": False},
            {"skill": "Plumbing", "type": "Tap Installation", "status": "completed", "assigned": False},
            # 1 En Route
            {"skill": "Plumbing", "type": "Drain Cleaning", "status": "en_route", "assigned": True},
            # 1 Assigned
            {"skill": "Plumbing", "type": "Water Heater Setup", "status": "assigned", "assigned": True},

            # Mechanical (3 total)
            # 1 Completed
            {"skill": "Mechanical", "type": "Pump Servicing", "status": "completed", "assigned": False},
            # 1 En Route
            {"skill": "Mechanical", "type": "Valves Maintenance", "status": "en_route", "assigned": True},
            # 1 Assigned
            {"skill": "Mechanical", "type": "Motor Alignment", "status": "assigned", "assigned": True},

            # Other (2 total)
            # 1 Completed
            {"skill": "Other", "type": "Network Cable Laying", "status": "completed", "assigned": False},
            # 1 Assigned
            {"skill": "Other", "type": "Router Rack Mounting", "status": "assigned", "assigned": True},
        ]

        customer_names = [
            "Amit Sharma", "Priya Patel", "Vikram Malhotra", "Sneha Reddy",
            "Rohan Das", "Anjali Iyer", "Sanjay Kapoor", "Neha Verma",
            "Karthik Raja", "Deepika Rao", "Arun Prasath", "Sunita Nair",
            "Rahul Dravid", "Meera Sen", "Aditya Roy", "Divya Joshi",
            "Suresh Raina", "Kavya Pillai", "Harish Kumar", "Preeti Saxena",
            "Vijay Sethupathi", "Abhishek Nair", "Ishaan Verma", "Ananya Mehta"
        ]

        busy_idx = 0
        for i, j_info in enumerate(jobs_to_seed):
            assigned_id = None
            if j_info["assigned"]:
                # Assign to one of the Busy technicians
                if busy_idx < len(busy_techs):
                    assigned_id = busy_techs[busy_idx].technician_id
                    busy_idx = (busy_idx + 1) % len(busy_techs)

            job = Job(
                customer_name=customer_names[i] if i < len(customer_names) else f"Customer {i+1}",
                location="13.0827,80.2707",
                issue_description=f"Standard field job for {j_info['type']}",
                priority="HIGH" if i % 2 == 0 else "MEDIUM",
                service_type=j_info["type"],
                contact_number="9876543210",
                preferred_service_date=datetime.date.today(),
                required_skill=j_info["skill"],
                status=j_info["status"],
                assigned_technician_id=assigned_id,
                tenant_id="tenant-1"
            )
            db.add(job)
        db.commit()

        print(f"Seeded {len(jobs_to_seed)} jobs.")

        # Seed initial GPSPing coordinates so they show up instantly in the UI
        print("Seeding GPS pings...")
        active_jobs = db.query(Job).filter(Job.assigned_technician_id.isnot(None)).all()
        for job in active_jobs:
            tech = db.query(Technician).filter(Technician.technician_id == job.assigned_technician_id).first()
            if tech:
                # Extract lat/lng from tech location string
                lat_str, lng_str = tech.technician_location.split(",")
                ping = GPSPing(
                    id=str(uuid.uuid4()),
                    technician_id=tech.tech_id,
                    job_id=str(job.id),
                    latitude=float(lat_str),
                    longitude=float(lng_str),
                    timestamp=datetime.datetime.now(datetime.timezone.utc),
                    accuracy=10.0,
                    altitude=0.0,
                    tenant_id="tenant-1"
                )
                db.add(ping)
        db.commit()

        print("Successfully seeded all data for the dashboard with active GPS tracking!")
    except Exception as e:
        import traceback
        traceback.print_exc()
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    seed_data()
