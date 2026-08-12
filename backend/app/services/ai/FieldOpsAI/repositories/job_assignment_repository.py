"""
job_assignment_repository.py

Repository responsible for JobAssignment database operations.

Responsibilities
----------------
- Store AI technician recommendations.
- Retrieve the current technician.
- Retrieve the next ranked technician.
- Update technician assignment status.

This repository contains NO business logic.
It only communicates with the database.
"""

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models import JobAssignment


class JobAssignmentRepository:
    """
    Repository for JobAssignment database operations.
    """

    def __init__(self, db: Session):
        self.db = db

    # ---------------------------------------------------------

    def save_recommendations(
        self,
        job_id: int,
        recommendations: List[dict],
    ) -> None:
        """
        Save the AI ranked technician recommendations.

        Rank 1 becomes the current candidate.
        """

        for recommendation in recommendations:

            assignment = JobAssignment(
                job_id=job_id,
                technician_id=recommendation["technician_id"],
                rank=recommendation["rank"],
                status="PENDING",
                is_current=(recommendation["rank"] == 1),
            )

            self.db.add(assignment)

    # ---------------------------------------------------------

    def get_current_candidate(
        self,
        job_id: int,
    ) -> Optional[JobAssignment]:
        """
        Return the technician currently being offered the job.
        """

        return (
            self.db.query(JobAssignment)
            .filter(
                JobAssignment.job_id == job_id,
                JobAssignment.is_current.is_(True),
            )
            .first()
        )

    # ---------------------------------------------------------

    def get_next_candidate(
        self,
        job_id: int,
    ) -> Optional[JobAssignment]:
        """
        Return the next pending technician.
        """

        current = self.get_current_candidate(job_id)

        if current is None:
            return None

        return (
            self.db.query(JobAssignment)
            .filter(
                JobAssignment.job_id == job_id,
                JobAssignment.rank > after_rank,
                JobAssignment.status == "PENDING",
            )
            .order_by(JobAssignment.rank)
            .first()
        )

    # ---------------------------------------------------------

    def mark_assigned(
        self,
        assignment: JobAssignment,
    ) -> None:
        """
        Mark that the technician has been offered the job.
        """

        assignment.assigned_at = datetime.now(timezone.utc)

    # ---------------------------------------------------------

    def mark_accepted(
        self,
        assignment: JobAssignment,
    ) -> None:
        """
        Mark technician as accepted.
        """

        assignment.status = "ACCEPTED"
        assignment.responded_at = datetime.now(timezone.utc)

    # ---------------------------------------------------------

    def mark_rejected(
        self,
        assignment: JobAssignment,
    ) -> None:
        """
        Mark technician as rejected.
        """

        assignment.status = "REJECTED"
        assignment.responded_at = datetime.now(timezone.utc)
        assignment.is_current = False

    # ---------------------------------------------------------

    def mark_timeout(
        self,
        assignment: JobAssignment,
    ) -> None:
        """
        Mark technician as timed out.
        """

        assignment.status = "TIMEOUT"
        assignment.responded_at = datetime.now(timezone.utc)
        assignment.is_current = False

    # ---------------------------------------------------------

    def promote_next_candidate(
        self,
        job_id: int,
        *,
        after_rank: int | None = None,
    ) -> Optional[JobAssignment]:
        """
        Make the next pending ranked technician the current candidate.
        """

        next_candidate = self.get_next_candidate(
            job_id,
            after_rank=after_rank,
        )

        if next_candidate is not None:
            next_candidate.is_current = True

        return next_candidate

    # ---------------------------------------------------------

    def get_rejected_technician_ids(
        self,
        job_id: int,
    ) -> List[int]:
        """
        Return all technicians who rejected or timed out.
        """

        rows = (
            self.db.query(JobAssignment)
            .filter(
                JobAssignment.job_id == job_id,
                JobAssignment.status.in_(["REJECTED", "TIMEOUT"]),
            )
            .all()
        )

        return [row.technician_id for row in rows]

    # ---------------------------------------------------------

    def get_remaining_candidates(
        self,
        *,
        job_id: int,
        after_rank: int,
    ) -> List[JobAssignment]:
        """
        Retrieve all pending later-ranked candidates for a job.
        """

        return (
            self.db.query(JobAssignment)
            .filter(
                JobAssignment.job_id == job_id,
                JobAssignment.status == "PENDING",
                JobAssignment.rank > after_rank,
            )
            .order_by(JobAssignment.rank)
            .all()
        )

    # ---------------------------------------------------------


    def save(self) -> None:
        """
        Commit pending changes.
        """

        self.db.commit()

    # ---------------------------------------------------------

    def refresh(
        self,
        assignment: JobAssignment,
    ) -> None:
        """
        Refresh object from database.
        """

        self.db.refresh(assignment)