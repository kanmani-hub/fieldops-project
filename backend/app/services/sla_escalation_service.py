import logging
import json
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models import Job, SLAEscalation, AuditEvent

logger = logging.getLogger(__name__)

class SLAEscalationService:
    @staticmethod
    def trigger_escalation(db: Session, job: Job):
        """
        Triggers the SLA escalation flow for a job.
        Marks job as ESCALATED, notifies manager, creates PagerDuty incident.
        """
        now = datetime.now(timezone.utc)
        
        # Avoid duplicate escalations
        if job.status.upper() == "ESCALATED":
            return
            
        old_status = job.status
        job.status = "ESCALATED"
        
        escalation = SLAEscalation(
            tenant_id=getattr(job, "tenant_id", None) or "default",
            job_id=job.id,
            manager_notified_at=now,
            status="ESCALATED"
        )
        db.add(escalation)
        
        audit = AuditEvent(
            tech_id="system",
            tenant_id="system",
            event_type="SLA_ESCALATION",
            old_status=old_status,
            new_status="ESCALATED",
            reason=f"SLA deadline at risk for {job.priority} job"
        )
        db.add(audit)
        
        # Simulate SMS and Email to Manager
        logger.info(f"SLAEscalationService: [SMS] Manager alerted for job {job.id} - SLA at risk!")
        logger.info(f"SLAEscalationService: [EMAIL] Manager alerted for job {job.id} - SLA at risk!")
        
        # Simulate Dashboard Notification
        # In a real scenario, this would use socket_manager to emit to 'dispatchers'
        logger.info(f"SLAEscalationService: [DASHBOARD] Emitting escalation alert for job {job.id}")
        
        # Create PagerDuty Incident
        SLAEscalationService.trigger_pagerduty_incident(job)
        
        db.commit()

    @staticmethod
    def trigger_pagerduty_incident(job: Job):
        """
        Simulates creating a PagerDuty incident for the job.
        """
        urgency = "critical" if job.priority == "P1" else "high"
        
        payload = {
            "incident": {
                "type": "incident",
                "title": f"{job.priority} Job SLA Risk - Re-dispatch Failed",
                "service": {
                    "id": "PAGERDUTY_SERVICE_ID",
                    "type": "service_reference"
                },
                "urgency": urgency,
                "body": {
                    "type": "incident_body",
                    "details": f"Job ID: {job.id}\nSLA Deadline: {job.sla_deadline}\nAttempts: {job.attempt_count}"
                },
                "escalation_policy": {
                    "id": "PAGERDUTY_POLICY_ID",
                    "type": "escalation_policy_reference"
                }
            }
        }
        logger.info(f"SLAEscalationService: [PAGERDUTY] Incident payload created: {json.dumps(payload)}")
        # In a real implementation, this makes a POST request to PagerDuty API
        
    @staticmethod
    def escalate_to_cto(db: Session, escalation: SLAEscalation):
        """
        Escalates to the CTO if the manager did not respond in 15 minutes.
        """
        now = datetime.now(timezone.utc)
        escalation.cto_notified_at = now
        escalation.status = "ESCALATED_TO_CTO"
        
        audit = AuditEvent(
            tech_id="system",
            tenant_id="system",
            event_type="CTO_ESCALATION",
            old_status="ESCALATED",
            new_status="ESCALATED_TO_CTO",
            reason=f"Manager did not respond within 15 minutes for job {escalation.job_id}"
        )
        db.add(audit)
        db.commit()
        
        logger.critical(f"SLAEscalationService: [CTO ESCALATION] CTO alerted for job {escalation.job_id}. No manager response.")
