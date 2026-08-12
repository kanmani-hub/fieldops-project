import os
import uuid
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

def seed_techs():
    from app.models import Technician
    db = SessionLocal()
    try:
        techs = db.query(Technician).all()
        print(f"Found {len(techs)} technicians in database.")
        for tech in techs:
            # Generate a valid UUID if none is set or if it is not a valid UUID format
            needs_update = False
            if not tech.tech_id:
                needs_update = True
            else:
                try:
                    uuid.UUID(tech.tech_id)
                except ValueError:
                    needs_update = True
            
            if needs_update:
                new_uuid = str(uuid.uuid4())
                print(f"Updating technician '{tech.technician_name}' (ID: {tech.technician_id}) with new tech_id: {new_uuid}")
                tech.tech_id = new_uuid
            
            if not tech.tenant_id:
                tech.tenant_id = "tenant-1"
                
            db.add(tech)
        db.commit()
        print("Successfully seeded all technicians with valid UUIDs and tenant-1 IDs!")
    except Exception as e:
        print(f"Error seeding technicians: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    seed_techs()
