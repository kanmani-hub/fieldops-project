from typing import Dict, Any
from sqlalchemy.orm import Session
import logging
from app.models import Technician
from app.redis_client import get_redis_client
import json

logger = logging.getLogger("fieldops")

class WorkloadScoringService:
    def calculate_workload_score(self, db: Session, tech_id: int, max_capacity: int = 3) -> Dict[str, Any]:
        """
        Calculate workload score (0-100) using 10s Redis cache.
        Enforces BR-002: max 3 concurrent jobs
        """
        redis_client = get_redis_client()
        cache_key = f"workload:tech:{tech_id}"
        
        # 1. Check Cache
        cached = redis_client.get(cache_key)
        if cached:
            active_jobs = int(cached)
        else:
            # 2. Query DB
            tech = db.query(Technician).filter(Technician.technician_id == tech_id).first()
            if not tech:
                return {"score": 0.0, "qualified": False, "active_jobs": 0, "reason": "Technician not found"}
            
            active_jobs = tech.current_jobs or 0
            
            # Cache the result for 10 seconds to handle rapid concurrent assignment ranking
            redis_client.setex(cache_key, 10, str(active_jobs))
            
        # 3. Disqualification / Alerting
        if active_jobs > max_capacity:
            logger.error(f"Data Inconsistency Alert: Technician {tech_id} has {active_jobs} active jobs, exceeding max {max_capacity}!")
            
        if active_jobs >= max_capacity:
            return {
                "score": 0.0,
                "qualified": False,
                "reason": "Maximum capacity reached (BR-002)",
                "active_jobs": active_jobs
            }
            
        # 4. Scoring Formula: (max - active) / max * 100
        score = round((max_capacity - active_jobs) / max_capacity * 100.0, 2)
        
        return {
            "score": score,
            "qualified": True,
            "active_jobs": active_jobs,
            "capacity_remaining": max_capacity - active_jobs
        }

    def increment_workload(self, db: Session, tech_id: int) -> int:
        """
        Atomically increments the technician's active job count using SELECT FOR UPDATE.
        Returns the new active job count.
        """
        tech = db.query(Technician).filter(Technician.technician_id == tech_id).with_for_update().first()
        if not tech:
            raise ValueError(f"Technician {tech_id} not found")
            
        current = tech.current_jobs or 0
        tech.current_jobs = current + 1
        
        # Flush DB and invalidate cache
        db.commit()
        redis_client = get_redis_client()
        redis_client.delete(f"workload:tech:{tech_id}")
        
        return tech.current_jobs

    def decrement_workload(self, db: Session, tech_id: int) -> int:
        """
        Atomically decrements the technician's active job count using SELECT FOR UPDATE.
        Ensures count does not drop below 0.
        Returns the new active job count.
        """
        tech = db.query(Technician).filter(Technician.technician_id == tech_id).with_for_update().first()
        if not tech:
            raise ValueError(f"Technician {tech_id} not found")
            
        current = tech.current_jobs or 0
        if current > 0:
            tech.current_jobs = current - 1
        
        # Flush DB and invalidate cache
        db.commit()
        redis_client = get_redis_client()
        redis_client.delete(f"workload:tech:{tech_id}")
        
        return tech.current_jobs
