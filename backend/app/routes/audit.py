from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
import logging

from app.database import get_db
from app.models import OverrideAuditEvent, Job, SecurityAuditLog
from app.schemas import OverrideAuditResponse
from app.dependencies.override_authorization import verify_jwt_token

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/audit",
    tags=["Audit"]
)

@router.get("/overrides/{job_id}", response_model=list[OverrideAuditResponse])
def get_override_audits_for_job(
    job_id: str,
    x_tenant_id: str = Header(..., alias="X-Tenant-ID"),
    authorization: str = Depends(verify_jwt_token),
    db: Session = Depends(get_db)
):
    try:
        job_db_id = int(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID format")

    # Verify job belongs to tenant
    job = db.query(Job).filter(Job.id == job_db_id, Job.tenant_id == x_tenant_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    audits = db.query(OverrideAuditEvent).filter(
        OverrideAuditEvent.job_id == job_db_id,
        OverrideAuditEvent.tenant_id == x_tenant_id
    ).order_by(OverrideAuditEvent.created_at.desc()).all()

    return audits


@router.get("/security")
def get_security_audit_logs(
    tenant_id: str,
    event_type: str = None,
    start_date: str = None,
    end_date: str = None,
    db: Session = Depends(get_db)
):
    query = db.query(SecurityAuditLog).filter(SecurityAuditLog.tenant_id == tenant_id)
    if event_type:
        query = query.filter(SecurityAuditLog.event == event_type)
    if start_date:
        from datetime import datetime
        try:
            dt = datetime.fromisoformat(start_date)
            query = query.filter(SecurityAuditLog.timestamp >= dt)
        except ValueError:
            query = query.filter(SecurityAuditLog.timestamp >= start_date)
    if end_date:
        from datetime import datetime
        try:
            dt = datetime.fromisoformat(end_date)
            query = query.filter(SecurityAuditLog.timestamp <= dt)
        except ValueError:
            query = query.filter(SecurityAuditLog.timestamp <= end_date)
    
    logs = query.order_by(SecurityAuditLog.timestamp.desc()).all()
    return [
        {
            "id": log.id,
            "event": log.event,
            "timestamp": log.timestamp.isoformat() if log.timestamp else None,
            "severity": log.severity,
            "user_tenant": log.user_tenant,
            "attempted_channel": log.attempted_channel,
            "ip_address": log.ip_address,
            "websocket_id": log.websocket_id,
            "action_taken": log.action_taken,
            "payload_tenant": log.payload_tenant,
            "target_tenant": log.target_tenant,
            "technician_id": log.technician_id,
            "job_id": log.job_id,
            "tenant_id": log.tenant_id
        }
        for log in logs
    ]

