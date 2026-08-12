import uuid
import logging
from fastapi import Request
from sqlalchemy.orm import Session
from app.models import OverrideAuditEvent
from app.dependencies.override_authorization import CurrentUser

logger = logging.getLogger(__name__)

def log_manual_override(
    db: Session,
    request: Request,
    current_user: CurrentUser,
    job_id: int,
    action: str,
    before_state: dict,
    after_state: dict,
    justification: str,
    reason: str,
    tenant_id: str
):
    ip_address = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("User-Agent", "unknown")
    correlation_id = request.headers.get("X-Correlation-ID")

    audit = OverrideAuditEvent(
        id=str(uuid.uuid4()),
        event_type="manual_override",
        actor_id=current_user.id,
        actor_role=current_user.role,
        actor_name=current_user.role.capitalize(), # Using role cap for name as requested per CurrentUser model
        job_id=job_id,
        action=action,
        before_state=before_state,
        after_state=after_state,
        justification=justification,
        reason=reason,
        ip_address=ip_address,
        user_agent=user_agent,
        correlation_id=correlation_id,
        tenant_id=tenant_id
    )
    
    db.add(audit)
    # The flush ensures it gets assigned and catches any synchronous DB issues, 
    # but the actual commit happens at the route level to stay atomic.
    db.flush() 
    logger.info(f"OverrideAuditEvent logged for job {job_id} by {current_user.id}")
