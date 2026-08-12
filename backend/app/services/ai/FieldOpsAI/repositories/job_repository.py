"""
job_repository.py

Repository responsible for Job database operations.

Responsibilities
----------------
- Retrieve jobs
- Update job assignment
- Update job status
- Persist changes

This repository contains NO business logic.
It only communicates with the database.
"""

from typing import Optional

from sqlalchemy.orm import Session

from app.models import Job


class JobRepository:
    """
    Repository for Job database operations.
    """

    def __init__(self, db: Session):
        self.db = db

    # ---------------------------------------------------------

    def get_by_id(
        self,
        job_id: int,
    ) -> Optional[Job]:
        """
        Retrieve a job by ID.
        """

        return (
            self.db.query(Job)
            .filter(Job.id == job_id)
            .first()
        )

    # ---------------------------------------------------------

    def assign_technician(
        self,
        job_id: int,
        technician_id: int,
    ) -> Optional[Job]:
        """
        Assign a technician to a job.

        Database commit is handled by the caller.
        """

        job = self.get_by_id(job_id)

        if job:

            job.assigned_technician_id = technician_id

        return job

    # ---------------------------------------------------------

    def update_status(
        self,
        job_id: int,
        status: str,
    ) -> Optional[Job]:
        """
        Update job status.

        Database commit is handled by the caller.
        """

        job = self.get_by_id(job_id)

        if job:

            job.status = status

        return job

    # ---------------------------------------------------------

    def save(
        self,
    ) -> None:
        """
        Commit pending database changes.
        """

        self.db.commit()

    # ---------------------------------------------------------

    def refresh(
        self,
        job: Job,
    ) -> None:
        """
        Refresh a Job object from the database.
        """

        self.db.refresh(job)