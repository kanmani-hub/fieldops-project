
"""Database-authoritative acceptance for broadcast job offers."""

from dataclasses import dataclass
from datetime import datetime, timezone
import uuid

from sqlalchemy.orm import Session

from app.models import (
    AuditEvent,
    InAppNotification,
    Job,
    JobAssignment,
    Technician,
)

OFFER_ACCEPTANCE_WINDOW_SECONDS = 5 * 60


@dataclass(frozen=True)
class AssignmentResult:
    job_id: int
    technician_id: int
    organization_id: str
    status: str = "ASSIGNED"
    already_accepted: bool = False


class AssignmentConflict(Exception):
    """The offer is invalid or another technician won the job."""


def accept_broadcast_offer(
    db: Session,
    *,
    job_id: int,
    technician_tenant_id: str,
    technician_id: int,
) -> AssignmentResult:
    """
    Atomically accept a technician's broadcast offer.

    Lock order:
        1. Job
        2. JobAssignment

    The caller owns the transaction and must commit or rollback.
    """

    now = datetime.now(timezone.utc)

    # Lock the shared job first. All acceptance/rejection paths must
    # use this same lock order.
    job = (
        db.query(Job)
        .filter(Job.id == job_id)
        .with_for_update()
        .first()
    )

    if job is None:
        raise AssignmentConflict("Job not found")

    # Idempotent retry: this technician already won.
    if (
        job.assigned_technician_id == technician_id
        and job.status in ("ASSIGNED", "ACCEPTED")
    ):
        accepted = (
            db.query(JobAssignment)
            .filter(
                JobAssignment.job_id == job_id,
                JobAssignment.technician_id == technician_id,
                JobAssignment.tenant_id == technician_tenant_id,
                JobAssignment.status == "ACCEPTED",
            )
            .first()
        )

        if accepted is not None:
            return AssignmentResult(
                job_id=job_id,
                technician_id=technician_id,
                organization_id=technician_tenant_id,
                already_accepted=True,
            )

    # Another technician already won.
    if job.assigned_technician_id is not None:
        raise AssignmentConflict(
            "This job has already been accepted by another technician"
        )

    if job.status not in (
        "CREATED",
        "QUEUED",
        "UNASSIGNED",
        "OFFERED",
        "REJECTED_BY_TECHNICIAN",
    ):
        raise AssignmentConflict(
            f"Job cannot be accepted in status {job.status}"
        )

    # Validate the recipient technician independently of the job tenant.
    technician = (
        db.query(Technician)
        .filter(
            Technician.technician_id == technician_id,
            Technician.tenant_id == technician_tenant_id,
        )
        .first()
    )

    if technician is None:
        raise AssignmentConflict("Technician not found in this organization")

    # Lock this technician's individual offer.
    offer = (
        db.query(JobAssignment)
        .filter(
            JobAssignment.job_id == job_id,
            JobAssignment.technician_id == technician_id,
            JobAssignment.tenant_id == technician_tenant_id,
            JobAssignment.status == "PENDING",
            JobAssignment.assigned_at.is_not(None),
        )
        .with_for_update()
        .first()
    )

    if offer is None:
        raise AssignmentConflict(
            "No active pending offer exists for this technician"
        )

    offer_created_at = offer.assigned_at
    if offer_created_at.tzinfo is None:
        offer_created_at = offer_created_at.replace(tzinfo=timezone.utc)
    else:
        offer_created_at = offer_created_at.astimezone(timezone.utc)
    if (now - offer_created_at).total_seconds() >= OFFER_ACCEPTANCE_WINDOW_SECONDS:
        raise AssignmentConflict("This offer has expired")

    # Claim the shared job. The job row is already locked.
    job.assigned_technician_id = technician_id
    job.status = "ASSIGNED"
    job.assigned_at = now

    offer.status = "ACCEPTED"
    offer.responded_at = now
    offer.is_current = False

    # Every other pending offer for this job loses.
    losing_offers = (
        db.query(JobAssignment)
        .filter(
            JobAssignment.job_id == job_id,
            JobAssignment.status == "PENDING",
            JobAssignment.id != offer.id,
        )
        .with_for_update()
        .all()
    )

    for losing_offer in losing_offers:
        losing_offer.status = "LOST"
        losing_offer.responded_at = now
        losing_offer.is_current = False

        losing_technician = (
            db.query(Technician)
            .filter(
                Technician.technician_id == losing_offer.technician_id,
                Technician.tenant_id == losing_offer.tenant_id,
            )
            .first()
        )

        if losing_technician is not None:
            recipient_id = str(
                losing_technician.tech_id
                or losing_technician.technician_id
            )

            db.add(
                InAppNotification(
                    id=str(uuid.uuid4()),
                    tenant_id=str(losing_technician.tenant_id),
                    tech_id=losing_technician.tech_id,
                    recipient_role="technician",
                    recipient_id=recipient_id,
                    group_key=f"job_{job_id}_lost",
                    job_id=str(job_id),
                    type="JOB_OFFER_LOST",
                    title="Job No Longer Available",
                    body=(
                        f"Job #{job_id} was accepted by another technician."
                    ),
                    status="UNREAD",
                    priority="NORMAL",
                    notification_metadata={
                        "event_id": (
                            f"job:{job_id}:offer-lost:"
                            f"{losing_offer.technician_id}"
                        )
                    },
                )
            )

    db.add(
        AuditEvent(
            tech_id=str(technician_id),
            tenant_id=technician_tenant_id,
            event_type="JOB_ASSIGNMENT_CONFIRMED",
            old_status="OFFERED",
            new_status="ASSIGNED",
            job_id=str(job_id),
            details={
                "technician_id": technician_id,
                "organization_id": technician_tenant_id,
            },
            timestamp=now,
        )
    )
    db.flush()


    # No commit here. The caller commits the entire transaction.
    return AssignmentResult(
        job_id=job_id,
        technician_id=technician_id,
        organization_id=technician_tenant_id,
    )
