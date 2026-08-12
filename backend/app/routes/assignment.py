from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from ..database import get_db
from .. import models, schemas, utils
from ..auth.dependencies import get_current_user_or_tenant, AuthenticatedUser

router = APIRouter(
    tags=["Assignment"]
)

@router.get("/technicians/match-skill", response_model=List[schemas.TechnicianResponse])
def match_skill(
    job_type: str,
    user_tenant: tuple[Optional[AuthenticatedUser], str] = Depends(get_current_user_or_tenant),
    db: Session = Depends(get_db)
):
    """
    Find available technicians matching the required skill (job_type).
    Falls back gracefully if exact match returns no results.
    """
    user, tenant_id = user_tenant
    pattern = f"%{job_type.strip()}%"
    tech_query = db.query(models.Technician).filter(
        models.Technician.technician_skill.ilike(pattern)
    )
    if not user or not user.is_super_admin:
        tech_query = tech_query.filter(models.Technician.tenant_id == tenant_id)

    technicians = tech_query.all()
    
    if not technicians:
        fallback_query = db.query(models.Technician)
        if not user or not user.is_super_admin:
            fallback_query = fallback_query.filter(models.Technician.tenant_id == tenant_id)
        technicians = fallback_query.all()
        
    return technicians

@router.get("/technicians/nearest", response_model=schemas.NearestTechnicianResponse)
def get_nearest_technician(
    job_id: int,
    user_tenant: tuple[Optional[AuthenticatedUser], str] = Depends(get_current_user_or_tenant),
    db: Session = Depends(get_db)
):
    """
    Identify the nearest available technician based on skill and location.
    """
    user, tenant_id = user_tenant
    job_query = db.query(models.Job).filter(models.Job.id == job_id)
    if not user or not user.is_super_admin:
        job_query = job_query.filter(models.Job.tenant_id == tenant_id)
    job = job_query.first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Filter technicians by skill, availability, workload, and tenant
    tech_query = db.query(models.Technician).filter(
        models.Technician.technician_skill == job.required_skill,
        models.Technician.technician_status.in_(["AVAILABLE", "ASSIGNED", "Available", "Assigned"]),
        models.Technician.current_jobs < models.Technician.max_jobs
    )
    if not user or not user.is_super_admin:
        tech_query = tech_query.filter(models.Technician.tenant_id == tenant_id)

    technicians = tech_query.all()

    if not technicians:
        raise HTTPException(
            status_code=404, 
            detail=f"No available technicians found with skill: {job.required_skill}"
        )

    # Calculate distances
    tech_distances = []
    for tech in technicians:
        dist = utils.calculate_distance(job.location, tech.technician_location)
        tech_distances.append((tech, dist))

    # Sort by distance
    tech_distances.sort(key=lambda x: x[1])
    
    nearest_tech, min_dist = tech_distances[0]
    
    return {
        "technician": nearest_tech,
        "distance": min_dist
    }

@router.post("/assign-job")
@router.post("/assign-technician")
def assign_job(
    assignment: schemas.TechnicianAssignment,
    user_tenant: tuple[Optional[AuthenticatedUser], str] = Depends(get_current_user_or_tenant),
    db: Session = Depends(get_db)
):
    """
    Assign a technician to a job with full validation.
    Checks:
    - Job existence & tenant boundary
    - Technician existence & tenant boundary
    - Technician availability (BUSY/OFFLINE)
    - Skill match
    - Duplicate assignment prevention
    """
    user, tenant_id = user_tenant
    try:
        # 1. Parse Job ID
        job_id_str = str(assignment.job_id)
        if job_id_str.upper().startswith('JOB'):
            job_id = int(job_id_str[3:])
        else:
            job_id = int(job_id_str)

        # 2. Fetch Job with tenant isolation
        job_query = db.query(models.Job).filter(models.Job.id == job_id)
        if not user or not user.is_super_admin:
            job_query = job_query.filter(models.Job.tenant_id == tenant_id)
        job = job_query.first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        # 3. Check for Duplicate Assignment
        if job.assigned_technician_id:
            raise HTTPException(
                status_code=400, 
                detail=f"Job #{job.id} is already assigned to technician #{job.assigned_technician_id}"
            )

        # 4. Fetch/Determine Technician with tenant isolation
        technician = None
        if assignment.technician_id is not None:
            tech_val = assignment.technician_id
            if isinstance(tech_val, int) or (isinstance(tech_val, str) and tech_val.isdigit()):
                t_q = db.query(models.Technician).filter(models.Technician.technician_id == int(tech_val))
                if not user or not user.is_super_admin:
                    t_q = t_q.filter(models.Technician.tenant_id == tenant_id)
                technician = t_q.first()
            if not technician:
                t_q = db.query(models.Technician).filter(models.Technician.tech_id == str(tech_val))
                if not user or not user.is_super_admin:
                    t_q = t_q.filter(models.Technician.tenant_id == tenant_id)
                technician = t_q.first()
            if not technician:
                raise HTTPException(status_code=404, detail="Technician not found")
        elif assignment.job_type:
            # Auto-assign logic based on skill and availability
            t_q = db.query(models.Technician).filter(
                models.Technician.technician_skill == assignment.job_type,
                models.Technician.technician_status.in_(["AVAILABLE", "ASSIGNED", "Available", "Assigned"]),
                models.Technician.current_jobs < models.Technician.max_jobs
            )
            if not user or not user.is_super_admin:
                t_q = t_q.filter(models.Technician.tenant_id == tenant_id)
            technicians = t_q.all()
            if not technicians:
                raise HTTPException(status_code=400, detail=f"No available technicians found with skill: {assignment.job_type}")
            technicians.sort(key=lambda t: t.current_jobs)
            technician = technicians[0]
        else:
            raise HTTPException(status_code=400, detail="Either technician_id or job_type must be provided")

        # 5. Comprehensive Validation (Workload, Status, Skill)
        from ..validation import validate_technician_for_assignment
        validate_technician_for_assignment(technician, job)

        # 7. Perform Assignment
        job.assigned_technician_id = technician.technician_id
        job.status = "ASSIGNED"
        
        from ..workload_utils import update_workload_count
        update_workload_count(db, technician.technician_id, 1)

        db.commit()
        db.refresh(job)
        db.refresh(technician)

        return {
            "message": "Technician assigned successfully",
            "job_id": job.id,
            "assigned_technician": {
                "id": technician.technician_id,
                "name": technician.technician_name,
                "skill": technician.technician_skill
            },
            "job_status": job.status
        }

    except HTTPException:
        raise
    except SQLAlchemyError as e:
        db.rollback()
        print(f"Database error during job assignment: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database connection error occurred"
        )
    except Exception as e:
        db.rollback()
        print(f"Unexpected error during job assignment: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )
