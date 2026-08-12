"""
job_closure_service.py
Service for processing job completion and fetching job closure details.
"""

from datetime import datetime, timezone
from typing import Optional
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from ..models import Job, Technician
from ..models.job_closure import JobClosure
from ..schemas import JobClosureCreate


def close_job(
    db: Session,
    job_id: int,
    closure_data: JobClosureCreate,
    technician_identifier: str,
    user_role: Optional[str] = "TECHNICIAN"
) -> JobClosure:
    """
    Closes a job with completion summary, images, and costs.

    Verification steps:
    1. verify job exists
    2. verify technician assignment
    3. verify job belongs to technician
    4. verify job not already completed
    5. calculate subtotal
    6. create JobClosure and update Job (status COMPLETED, completed_at, completed_by)
    7. single transaction with rollback on failure
    """
    # 0. Enforce role restriction: Technician or Dispatcher/Admin/Manager
    role_str = (user_role or "").upper()
    allowed_roles = ["TECHNICIAN", "DISPATCHER", "ADMIN", "SUPER_ADMIN", "MANAGER", "LEAD"]
    if role_str not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only technicians, dispatchers, or administrators can close jobs"
        )

    # 1. Verify job exists
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )

    # 2. Verify technician assignment
    if job.assigned_technician_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job is not assigned to any technician"
        )

    # 3. Verify job belongs to technician if requested by a technician
    tech = db.query(Technician).filter(
        (Technician.technician_id == job.assigned_technician_id)
    ).first()

    if role_str == "TECHNICIAN":
        tech_matches = False
        if tech:
            if (
                str(tech.technician_id) == str(technician_identifier)
                or tech.tech_id == str(technician_identifier)
                or str(job.assigned_technician_id) == str(technician_identifier)
                or (tech.phone_number and tech.phone_number == str(technician_identifier))
                or (tech.technician_name and tech.technician_name == str(technician_identifier))
            ):
                tech_matches = True

        # Fallback check if technician_identifier directly matches assigned_technician_id
        if not tech_matches and str(job.assigned_technician_id) == str(technician_identifier):
            tech_matches = True

        if not tech_matches:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Job is not assigned to this technician"
            )

    # 4. Verify job not already completed
    if (job.status or "").upper() == "COMPLETED" or job.completed_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job is already completed"
        )

    # Check if a JobClosure record already exists
    existing_closure = db.query(JobClosure).filter(
        JobClosure.job_id == job_id,
        JobClosure.tenant_id == job.tenant_id,
    ).first()
    if existing_closure:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job closure record already exists for this job"
        )

    # 5. Calculate subtotal
    subtotal = round(closure_data.labour_cost + closure_data.material_cost, 2)
    now = datetime.now(timezone.utc)

    # 6. Single transaction execution
    try:
        closure_record = JobClosure(
            job_id=job.id,
            tenant_id=job.tenant_id,
            work_summary=closure_data.work_summary,
            before_images=closure_data.before_images or [],
            after_images=closure_data.after_images,
            labour_cost=closure_data.labour_cost,
            material_cost=closure_data.material_cost,
            subtotal=subtotal,
            completed_at=now,
        )
        db.add(closure_record)

        # Update Job record
        job.status = "COMPLETED"
        job.completed_at = now
        job.completed_by = str(technician_identifier)

        # Update technician status
        if tech:
            tech.technician_status = "AVAILABLE"
            tech.current_jobs = max(0, (tech.current_jobs or 1) - 1)

        db.commit()
        db.refresh(closure_record)
        return closure_record
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to close job due to database error: {str(e)}"
        )


def get_job_closure(db: Session, job_id: int) -> JobClosure:
    """
    Fetch closure details for a given job.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )

    closure = db.query(JobClosure).filter(
        JobClosure.job_id == job_id,
        JobClosure.tenant_id == job.tenant_id,
    ).first()
    if not closure:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job closure details not found for this job"
        )

    return closure
