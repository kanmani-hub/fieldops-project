from fastapi import Depends, HTTPException, Header, Request
from sqlalchemy.orm import Session
from dataclasses import dataclass
import logging

from app.database import get_db
from app.redis_client import get_redis_client
from app.models import AuditEvent
from app.routes.dispatch import verify_jwt_token

logger = logging.getLogger(__name__)

@dataclass
class CurrentUser:
    id: str
    role: str
    ip: str

def get_current_user(
    request: Request,
    authorization: str = Depends(verify_jwt_token),
    x_permissions: str = Header(default="technician", alias="X-Permissions")
) -> CurrentUser:
    """
    Mock extracting the user and role. In production, this would parse the JWT.
    Here we rely on X-Permissions acting as the gateway-injected role header.
    """
    return CurrentUser(
        id=authorization, # Using the token string as the ID for now
        role=x_permissions.lower(),
        ip=request.client.host if request.client else "unknown"
    )

def require_override_role():
    allowed = {"dispatcher", "manager", "tenant_admin", "super_admin"}
    
    def checker(
        user: CurrentUser = Depends(get_current_user),
        db: Session = Depends(get_db),
        redis_client = Depends(get_redis_client)
    ):
        # Rate Limiting: 10 attempts per minute per user
        if redis_client:
            rate_key = f"rate_limit:override:{user.id}"
            req_count = redis_client.incr(rate_key)
            if req_count == 1:
                redis_client.expire(rate_key, 60)
            if req_count > 10:
                logger.warning(f"User {user.id} exceeded override rate limit")
                raise HTTPException(status_code=429, detail="Too many override attempts. Limit 10 per minute.")
                
        # RBAC Check
        if user.role not in allowed:
            logger.warning(
                f"Unauthorized override attempt",
                extra={"actor_id": user.id, "role": user.role, "ip_address": user.ip}
            )
            
            # Log to audit trail
            audit = AuditEvent(
                tech_id=user.id,
                tenant_id="system",
                event_type="UNAUTHORIZED_OVERRIDE_ATTEMPT",
                old_status="N/A",
                new_status="N/A",
                reason=f"Role '{user.role}' attempted manual override"
            )
            db.add(audit)
            db.commit()
            
            raise HTTPException(
                status_code=403,
                detail=f"Role '{user.role}' not authorized for manual override. Required: dispatcher, manager, tenant_admin, or super_admin."
            )
        return user
        
    return checker
