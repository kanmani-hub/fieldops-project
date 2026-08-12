import logging
from typing import Optional, Dict, Any
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

class CooldownService:
    @staticmethod
    def set_cooldown(redis_client, job_id: str, tech_id: str, duration_seconds: int = 120) -> bool:
        """
        Sets a cooldown preventing the technician from being assigned to this job again.
        """
        if not redis_client:
            return False
            
        key = f"job:cooldown:{job_id}:{tech_id}"
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)).isoformat()
        
        result = redis_client.setex(key, duration_seconds, expires_at)
        if result:
            logger.info(f"CooldownService: Applied {duration_seconds}s cooldown for tech {tech_id} on job {job_id}")
            return True
        return False

    @staticmethod
    def check_cooldown(redis_client, job_id: str, tech_id: str) -> Optional[Dict[str, Any]]:
        """
        Checks if a cooldown exists. Returns dict with 'remaining_seconds' and 'expires_at' if active, else None.
        """
        if not redis_client:
            return None
            
        key = f"job:cooldown:{job_id}:{tech_id}"
        
        expires_at_str = redis_client.get(key)
        
        if expires_at_str:
            try:
                expires_at = datetime.fromisoformat(expires_at_str)
                now = datetime.now(timezone.utc)
                remaining = int((expires_at - now).total_seconds())
                
                if remaining <= 0:
                    return None
                    
                return {
                    "cooldown_expires_at": expires_at_str,
                    "remaining_seconds": remaining
                }
            except Exception as e:
                logger.error(f"CooldownService: Error parsing expiry for {key}: {e}")
                return None
                
        return None

    @staticmethod
    def clear_cooldown(redis_client, job_id: str, tech_id: str) -> bool:
        """
        Clears the cooldown for manual admin overrides.
        """
        if not redis_client:
            return False
            
        key = f"job:cooldown:{job_id}:{tech_id}"
        result = redis_client.delete(key)
        if result:
            logger.info(f"CooldownService: Cleared cooldown manually for tech {tech_id} on job {job_id}")
            return True
        return False
