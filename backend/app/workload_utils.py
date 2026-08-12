from sqlalchemy.orm import Session
from . import models

def sync_technician_status(technician: models.Technician):
    """
    Synchronize technician status based on current workload.
    """
    if technician.current_jobs >= technician.max_jobs:
        technician.technician_status = "BUSY"
    elif technician.technician_status == "BUSY" and technician.current_jobs < technician.max_jobs:
        # Only set to AVAILABLE if they were previously BUSY 
        # (don't override OFFLINE etc.)
        technician.technician_status = "AVAILABLE"

def update_workload_count(db: Session, technician_id: int, delta: int):
    """
    Update workload count and sync status.
    delta can be positive or negative.
    """
    tech = db.query(models.Technician).filter(models.Technician.technician_id == technician_id).first()
    if tech:
        tech.current_jobs = max(0, tech.current_jobs + delta)
        sync_technician_status(tech)
        db.add(tech)
        return tech
    return None
