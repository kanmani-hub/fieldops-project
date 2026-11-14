
"""
job_assignment_repository.py

Repository responsible for JobAssignment database operations.

Supports concurrent technician broadcast offers.

Each JobAssignment represents one technician's offer for a job.
The technician's tenant_id is stored on that individual offer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import JobAssignment


class JobAssignmentRepository:
    def __init__(self, db: Session):
        self.db = db

    # ---------------------------------------------------------
    # CREATE
    # ---------------------------------------------------------

    def save_recommendations(
        self,
        job_id: int,
        recommendations: list[dict],
    ) -> None:
        """
        Store recommendations.

        Each recommendation must contain:
            technician_id
            tenant_id       # technician's tenant
            rank

        These are recommendations, not active offers.
        assigned_at remains NULL until the offer is sent.
        """
        for recommendation in recommendations:
            assignment = JobAssignment(
                job_id=int(job_id),
                tenant_id=str(recommendation["tenant_id"]),
                technician_id=int(recommendation["technician_id"]),
                rank=int(recommendation["rank"]),
                status="PENDING",
                assigned_at=None,
                responded_at=None,
                is_current=False,
            )
            self.db.add(assignment)

    # ---------------------------------------------------------
    # READ
    # ---------------------------------------------------------

    def get_offer(
        self,
        *,
        job_id: int,
        technician_id: int,
        technician_tenant_id: str,
    ) -> Optional[JobAssignment]:
        """Retrieve one technician's offer within their tenant."""
        return (
            self.db.query(JobAssignment)
            .filter(
                JobAssignment.job_id == job_id,
                JobAssignment.technician_id == technician_id,
                JobAssignment.tenant_id == technician_tenant_id,
            )
            .order_by(JobAssignment.created_at.desc())
            .first()
        )

    def get_pending_offers(
        self,
        job_id: int,
    ) -> List[JobAssignment]:
        """Return all active, already-sent offers for a job."""
        return (
            self.db.query(JobAssignment)
            .filter(
                JobAssignment.job_id == job_id,
                func.upper(JobAssignment.status) == "PENDING",
                JobAssignment.assigned_at.isnot(None),
            )
            .order_by(JobAssignment.rank.asc())
            .all()
        )

    def get_all_offers(
        self,
        job_id: int,
    ) -> List[JobAssignment]:
        """Return every offer/recommendation for a job."""
        return (
            self.db.query(JobAssignment)
            .filter(JobAssignment.job_id == job_id)
            .order_by(JobAssignment.rank.asc())
            .all()
        )

    def get_remaining_candidates(
        self,
        *,
        job_id: int,
        after_rank: int = 0,
    ) -> List[JobAssignment]:
        """
        Return recommendations that have not yet been offered.

        Only rows with assigned_at=NULL are eligible.
        """
        return (
            self.db.query(JobAssignment)
            .filter(
                JobAssignment.job_id == job_id,
                func.upper(JobAssignment.status) == "PENDING",
                JobAssignment.assigned_at.is_(None),
                JobAssignment.rank > after_rank,
            )
            .order_by(JobAssignment.rank.asc())
            .all()
        )

    def get_rejected_technician_ids(
        self,
        job_id: int,
    ) -> List[int]:
        """
        Return technicians who must not be offered this job again.
        """
        rows = (
            self.db.query(JobAssignment.technician_id)
            .filter(
                JobAssignment.job_id == job_id,
                func.upper(JobAssignment.status).in_(
                    ["REJECTED", "TIMEOUT", "LOST", "ACCEPTED"]
                ),
            )
            .distinct()
            .all()
        )

        return [int(row[0]) for row in rows]

    def get_pending_count(self, job_id: int) -> int:
        """Count active broadcast offers."""
        return (
            self.db.query(JobAssignment)
            .filter(
                JobAssignment.job_id == job_id,
                func.upper(JobAssignment.status) == "PENDING",
                JobAssignment.assigned_at.isnot(None),
            )
            .count()
        )

    def are_all_offers_terminal(self, job_id: int) -> bool:
        """
        True when no sent offer remains pending.

        The coordinator should use this before starting another batch.
        """
        return self.get_pending_count(job_id) == 0

    # ---------------------------------------------------------
    # STATE UPDATES
    # ---------------------------------------------------------

    def mark_assigned(
        self,
        assignment: JobAssignment,
    ) -> None:
        """
        Mark this technician's offer as sent.

        Multiple offers can simultaneously have is_current=True.
        """
        assignment.assigned_at = datetime.now(timezone.utc)
        assignment.status = "PENDING"
        assignment.is_current = True

    def mark_accepted(
        self,
        assignment: JobAssignment,
    ) -> None:
        assignment.status = "ACCEPTED"
        assignment.responded_at = datetime.now(timezone.utc)
        assignment.is_current = False

    def mark_rejected(
        self,
        assignment: JobAssignment,
    ) -> None:
        assignment.status = "REJECTED"
        assignment.responded_at = datetime.now(timezone.utc)
        assignment.is_current = False

    def mark_timeout(
        self,
        assignment: JobAssignment,
    ) -> None:
        assignment.status = "TIMEOUT"
        assignment.responded_at = datetime.now(timezone.utc)
        assignment.is_current = False

    def mark_lost(
        self,
        assignment: JobAssignment,
    ) -> None:
        assignment.status = "LOST"
        assignment.responded_at = datetime.now(timezone.utc)
        assignment.is_current = False

    # ---------------------------------------------------------
    # TRANSACTION
    # ---------------------------------------------------------

    def save(self) -> None:
        self.db.commit()

    def flush(self) -> None:
        self.db.flush()

    def refresh(self, assignment: JobAssignment) -> None:
        self.db.refresh(assignment)
