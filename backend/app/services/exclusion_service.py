import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class ExclusionService:
    @staticmethod
    def is_excluded(redis_client, job_id: str, tech_id: str) -> dict:
        """
        Checks if a technician is excluded from a job via active cooldown or historical rejection.
        Returns a dict with {"excluded": bool, "reason": str}
        """
        if not redis_client:
            return {"excluded": False}

        # Check active cooldown
        if redis_client.exists(f"job:cooldown:{job_id}:{tech_id}"):
            return {"excluded": True, "reason": "cooldown_active"}

        # Check historical exclusion list
        if redis_client.sismember(f"job:excluded:{job_id}", tech_id):
            return {"excluded": True, "reason": "previously_rejected"}

        return {"excluded": False}

    @staticmethod
    def add_exclusion(redis_client, job_id: str, tech_id: str, reason: str) -> None:
        """
        Adds a technician to the historical exclusion list for a job.
        """
        if not redis_client or not tech_id:
            return

        now_str = datetime.now(timezone.utc).isoformat()
        try:
            redis_client.sadd(f"job:excluded:{job_id}", tech_id)
            redis_client.hset(f"job:exclusion_reasons:{job_id}", tech_id, reason)
            redis_client.hset(f"job:exclusion_times:{job_id}", tech_id, now_str)
            logger.info(f"ExclusionService: Added tech {tech_id} to exclusion for job {job_id} (Reason: {reason})")
        except Exception as e:
            logger.error(f"ExclusionService: Failed to add exclusion for tech {tech_id} on job {job_id}: {e}")

    @staticmethod
    def get_exclusion_details(redis_client, job_id: str, tech_id: str) -> dict:
        """
        Retrieves the reason and time of exclusion if present.
        """
        if not redis_client:
            return {}

        reason = redis_client.hget(f"job:exclusion_reasons:{job_id}", tech_id)
        time_str = redis_client.hget(f"job:exclusion_times:{job_id}", tech_id)
        
        return {
            "rejection_reason": reason,
            "rejected_at": time_str
        }

    @staticmethod
    def clear_exclusions(redis_client, job_id: str) -> None:
        """
        Clears the exclusion list when a job is closed or completed.
        """
        if not redis_client:
            return

        try:
            redis_client.delete(f"job:excluded:{job_id}")
            redis_client.delete(f"job:exclusion_reasons:{job_id}")
            redis_client.delete(f"job:exclusion_times:{job_id}")
            logger.info(f"ExclusionService: Cleared exclusions for job {job_id}")
        except Exception as e:
            logger.error(f"ExclusionService: Failed to clear exclusions for job {job_id}: {e}")
