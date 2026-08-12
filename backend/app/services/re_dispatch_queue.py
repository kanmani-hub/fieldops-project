import logging
from datetime import datetime, timezone
from typing import Dict, Any, Tuple
from sqlalchemy.orm import Session

from app.models import Job, AuditEvent
from app.services.exclusion_service import ExclusionService
from app.services.dispatcher_alert_service import DispatcherAlertService

logger = logging.getLogger(__name__)

class ReDispatchQueueService:

    @classmethod
    def calculate_new_priority(cls, current_priority: str, attempt_count: int, already_bumped: bool) -> Tuple[str, bool]:
        """
        Calculates new priority based on escalation rules.
        Max bump is 1 level up.
        """
        if already_bumped:
            return current_priority, False
            
        new_priority = current_priority
        bumped = False
        
        if current_priority == "P2" and attempt_count >= 2:
            new_priority = "P1"
            bumped = True
        elif current_priority == "P3" and attempt_count >= 1:
            new_priority = "P2"
            bumped = True
        elif current_priority == "P4" and attempt_count >= 1:
            new_priority = "P3"
            bumped = True
            
        return new_priority, bumped

    @classmethod
    def calculate_priority_score(cls, priority: str, created_at: datetime) -> float:
        """
        Higher score = higher priority.
        Tie-break: older jobs first (lower timestamp = higher score).
        """
        base = {"P1": 1000, "P2": 800, "P3": 600, "P4": 400, "P5": 200}
        
        if created_at.tzinfo is None:
            created_at_utc = created_at.replace(tzinfo=timezone.utc)
        else:
            created_at_utc = created_at.astimezone(timezone.utc)
            
        # We subtract the timestamp so that earlier dates result in a higher value
        # However, Redis sorted sets order from lowest to highest score by default.
        # But we can query with ZREVRANGE to get highest score first.
        return base.get(priority, 400) - created_at_utc.timestamp()

    @classmethod
    def enqueue_failed_job(
        cls,
        db: Session,
        redis_client,
        job: Job,
        tenant_id: str,
        reason: str,
        tech_id: str | None = None,
    ) -> Dict[str, Any]:
        """
        Move a failed assignment back to QUEUED.

        Database changes are flushed but not committed. The calling route
        controls the final transaction so the job, audit records, technician
        update, and notification are saved atomically.
        """

        now = datetime.now(timezone.utc)

        # Always derive the trusted tenant from the validated job.
        effective_tenant_id = job.tenant_id

        if not effective_tenant_id:
            raise ValueError(f"Job {job.id} does not have a tenant_id" )
        # Detect an incorrect caller instead of accepting another tenant.
        if tenant_id != effective_tenant_id:
            raise ValueError("Tenant mismatch while requeuing job")
        if tech_id and redis_client:
            ExclusionService.add_exclusion(
                redis_client,
                str(job.id),
                tech_id,
                reason,
            )

        new_attempt_count = (job.attempt_count or 0) + 1
        already_bumped = job.bumped_at is not None

        new_priority, bumped = cls.calculate_new_priority(
            job.priority,
            new_attempt_count,
            already_bumped,
        )

        old_status = job.status
        old_priority = job.priority

        # Return the job to the unassigned queue.
        job.status = "QUEUED"
        job.assigned_technician_id = None
        job.attempt_count = new_attempt_count

        if bumped:
            job.previous_priority = old_priority
            job.priority = new_priority
            job.bumped_at = now

            logger.info(
                "ReDispatchQueue: Bumping job %s priority %s -> %s",
                job.id,
                old_priority,
                new_priority,
            )

        DispatcherAlertService.check_and_trigger_alert(
            db,
            redis_client,
            job,
        )

        audit = AuditEvent(
            tech_id=tech_id or "system",
            tenant_id=effective_tenant_id,
            event_type="JOB_REQUEUED",
            old_status=old_status,
            new_status="QUEUED",
            reason=(
                f"{reason} "
                f"(Attempt: {new_attempt_count}, "
                f"Priority: {new_priority})"
            ),
        )
        db.add(audit)

        # Execute SQL validation without committing the transaction.
        db.flush()

        score = cls.calculate_priority_score(
            job.priority,
            job.created_at,
        )

        queue_key = (
            f"dispatch:queue:{effective_tenant_id}"
        )

        if redis_client:
            redis_client.zadd(
                queue_key,
                {str(job.id): score},
            )

            redis_client.incr(
                "metrics:queue_insertions"
            )

            queue_depth = redis_client.zcard(
                queue_key
            )

            redis_client.set(
                "metrics:queue_depth",
                queue_depth,
            )

        logger.info(
            "ReDispatchQueue: Enqueued job %s to %s "
            "with score %.2f",
            job.id,
            queue_key,
            score,
        )

        return {
            "job_id": job.id,
            "new_status": job.status,
            "priority": job.priority,
            "bumped": bumped,
            "attempt_count": new_attempt_count,
            "queue_score": score,
        }
