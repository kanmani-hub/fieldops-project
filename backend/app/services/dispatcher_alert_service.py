import logging
import uuid
import json
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models import Job, DispatcherAlert, Technician
from app.services.exclusion_service import ExclusionService

logger = logging.getLogger(__name__)

class DispatcherAlertService:
    @staticmethod
    def check_and_trigger_alert(db: Session, redis_client, job: Job):
        """
        Checks if the attempt count warrants an alert, and triggers it if so.
        Logic: 
        - Trigger at attempt == 3 (WARNING)
        - Skip at attempt == 4
        - Trigger at attempt >= 5 every 2 attempts (CRITICAL)
        """
        attempt = job.attempt_count or 0
        
        if attempt < 3:
            return
            
        # Deduplication logic: alert at 3, 5, 7...
        if attempt >= 5 and (attempt - 3) % 2 != 0:
            return
        if attempt == 4:
            return
            
        severity = "CRITICAL" if attempt >= 5 else "WARNING"
        
        # Get excluded technicians
        excluded_techs_data = []
        if redis_client:
            tech_ids = redis_client.smembers(f"job:excluded:{job.id}")
            for tid_bytes in tech_ids:
                tid = tid_bytes.decode('utf-8') if isinstance(tid_bytes, bytes) else tid_bytes
                
                # Fetch name
                tech = db.query(Technician).filter(Technician.tech_id == tid).first()
                name = tech.technician_name if tech else f"Tech {tid}"
                
                reason = redis_client.hget(f"job:exclusion_reasons:{job.id}", tid)
                reason_str = reason.decode('utf-8') if isinstance(reason, bytes) else (reason or "Unknown")
                
                excluded_techs_data.append({"name": name, "reason": reason_str})
                
        alert_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        
        alert = DispatcherAlert(
            id=alert_id,
            tenant_id=job.organization_id,
            type="repeated_redispatch",
            severity=severity,
            job_id=job.id,
            attempt_count=attempt,
            max_attempts=5,
            excluded_technicians=excluded_techs_data,
            recommended_action="Manual assignment or job review",
            acknowledged=0,
            created_at=now
        )
        db.add(alert)
        db.commit()
        
        # Dashboard notification
        payload = {
            "alert_id": alert_id,
            "type": "repeated_redispatch",
            "severity": severity,
            "job_id": str(job.id),
            "job_title": f"{job.service_type} - {job.location}",
            "attempt_count": attempt,
            "max_attempts": 5,
            "excluded_technicians": excluded_techs_data,
            "recommended_action": "Manual assignment or job review",
            "created_at": now.isoformat(),
            "acknowledged": False
        }
        
        logger.info(f"DispatcherAlertService: [DASHBOARD] Broadcasting alert {alert_id} (Severity: {severity}) for job {job.id}")
        
        # Emit alert to all clients via Socket.IO
        import asyncio
        from app.services.socket_manager import sio
        
        async def broadcast():
            try:
                await sio.emit("redispatch:alert", payload)
            except Exception as se:
                logger.error(f"Failed to emit socket.io alert: {se}")
                
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(broadcast())
        except RuntimeError:
            new_loop = asyncio.new_event_loop()
            new_loop.run_until_complete(broadcast())
            new_loop.close()
        
        # Simulate Email to dispatchers/managers
        logger.info(f"DispatcherAlertService: [EMAIL] Alert sent for job {job.id} - Attempt {attempt} - Action Required")
