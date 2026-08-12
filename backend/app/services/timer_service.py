import json
from datetime import datetime, timezone, timedelta
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class TimerService:
    @staticmethod
    def start_timer(redis_client, job_id: str, tech_id: str, duration_seconds: int = 600) -> bool:
        """
        Start the 10-minute acceptance timer and the 2-minute warning timer.
        """
        if not redis_client:
            logger.warning("TimerService: No Redis client available, skipping timer.")
            return False
            
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=duration_seconds)
        warning_at = now + timedelta(seconds=duration_seconds - 120)
        
        timer_data = {
            "expires_at": expires_at.isoformat(),
            "job_id": str(job_id),
            "tech_id": str(tech_id)
        }
        
        warning_data = {
            "warning_at": warning_at.isoformat(),
            "job_id": str(job_id),
            "tech_id": str(tech_id)
        }
        
        # job:timer
        redis_client.setex(
            f"job:timer:{job_id}",
            duration_seconds,
            json.dumps(timer_data)
        )
        
        # job:timer_warning (expires when warning should fire, i.e., 480 seconds)
        redis_client.setex(
            f"job:timer_warning:{job_id}",
            duration_seconds - 120,
            json.dumps(warning_data)
        )
        
        logger.info(f"TimerService: Started timer for job {job_id} (tech {tech_id}) with duration {duration_seconds}s")
        return True

    @staticmethod
    def cancel_timer(redis_client, job_id: str) -> bool:
        """
        Cancel the acceptance timer and warning timer.
        """
        if not redis_client:
            return False
            
        res1 = redis_client.delete(f"job:timer:{job_id}")
        res2 = redis_client.delete(f"job:timer_warning:{job_id}")
        redis_client.delete(f"job:timer_warned:{job_id}") # flag to prevent repeat warnings
        
        if res1 or res2:
            logger.info(f"TimerService: Cancelled timer for job {job_id}")
            return True
        return False
