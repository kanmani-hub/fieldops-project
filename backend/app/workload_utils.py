"""Workload helpers for tenant-scoped technicians."""

from __future__ import annotations

from sqlalchemy.orm import Session

from . import models


WORKLOAD_LIMIT = 5

# Keep this set normalized (uppercase, with separators removed).
ACCEPTED_OPEN_STATUSES = frozenset(
    {
        "ACCEPTED",
        "ENROUTE",
        "ONSITE",
        "INPROGRESS",
        "PAUSED",
        "ONHOLD",
        "HOLD",
        "ESCALATED",
    }
)


def sync_technician_status(technician: models.Technician):
    """Compatibility helper; workload does not change availability status."""
    return technician


def _normalized_status(value: str | None) -> str:
    return (
        str(value or "")
        .strip()
        .upper()
        .replace("_", "")
        .replace("-", "")
        .replace(" ", "")
    )


def get_technician(
    db: Session,
    technician_id: int,
    tenant_id: str | None = None,
    *,
    for_update: bool = False,
) -> models.Technician | None:
    """Fetch a technician using the tenant + numeric ID composite identity."""
    query = db.query(models.Technician).filter(
        models.Technician.technician_id == int(technician_id)
    )
    if tenant_id is not None:
        query = query.filter(models.Technician.tenant_id == str(tenant_id))
    if for_update:
        query = query.with_for_update()
    return query.first()


def get_current_workload(
    db: Session,
    technician_id: int,
    tenant_id: str | None = None,
) -> int:
    """Return the stored technicians.current_jobs value."""
    technician = get_technician(db, technician_id, tenant_id)
    if technician is None:
        return 0
    try:
        return max(0, int(technician.current_jobs or 0))
    except (TypeError, ValueError):
        return 0


def update_workload_count(
    db: Session,
    technician_id: int,
    delta: int,
    tenant_id: str | None = None,
):
    """Apply a workload delta to the tenant-scoped technician row."""
    technician = get_technician(
        db, technician_id, tenant_id, for_update=True
    )
    if technician is None:
        return None

    current = max(0, int(technician.current_jobs or 0))
    technician.current_jobs = max(0, current + int(delta))
    db.add(technician)
    return technician


def count_accepted_open_jobs(
    db: Session,
    technician_id: int,
    tenant_id: str | None = None,
) -> int:
    """Count accepted, unfinished jobs for a tenant-scoped technician.

    The optional tenant_id keeps older two-argument call sites compatible.
    """
    query = db.query(models.Job).filter(
        models.Job.assigned_technician_id == int(technician_id)
    )
    if tenant_id is not None:
        query = query.filter(models.Job.tenant_id == str(tenant_id))

    jobs = query.all()
    return sum(
        1
        for job in jobs
        if _normalized_status(job.status) in ACCEPTED_OPEN_STATUSES
    )


def reconcile_workload_count(
    db: Session,
    technician_id: int,
    tenant_id: str | None = None,
):
    """Recalculate current_jobs from accepted/open jobs and persist in-session."""
    technician = get_technician(
        db, technician_id, tenant_id, for_update=True
    )
    if technician is None:
        return None

    technician.current_jobs = count_accepted_open_jobs(
        db,
        technician_id,
        str(technician.tenant_id),
    )
    db.add(technician)
    return technician
