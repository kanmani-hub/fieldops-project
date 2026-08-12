import logging
from datetime import datetime
from typing import Dict, Any, List
from sqlalchemy.orm import Session

from ..models import Technician, Job, AuditEvent

logger = logging.getLogger(__name__)

class CertificationValidator:
    def get_expired_and_expiring_certifications(self, technician: Technician) -> Dict[str, List[str]]:
        if not technician.certifications_data:
            return {"expired": [], "expiring_soon": []}
            
        expired = []
        expiring_soon = []
        now = datetime.now()
        thirty_days = now.timestamp() + (30 * 24 * 60 * 60)
        
        for skill, exp_date_str in technician.certifications_data.items():
            try:
                exp_date = datetime.fromisoformat(exp_date_str)
                if exp_date < now:
                    expired.append(skill.upper())
                elif exp_date.timestamp() < thirty_days:
                    expiring_soon.append(skill.upper())
            except ValueError:
                logger.error(f"Invalid date format for {skill} on technician {technician.tech_id}")
                expired.append(skill.upper())
                
        return {"expired": expired, "expiring_soon": expiring_soon}

    def get_prerequisites(self, skill: str, visited: set = None) -> List[str]:
        if visited is None:
            visited = set()
        skill_upper = skill.upper()
        if skill_upper in visited:
            return []
        visited.add(skill_upper)
        
        mapping = {
            "HVAC_CERT": ["ELEC_LV"],
            "A": ["B"],
            "B": ["C"]
        }
        direct = mapping.get(skill_upper, [])
        all_prereqs = list(direct)
        for p in direct:
            all_prereqs.extend(self.get_prerequisites(p, visited))
        return list(set(all_prereqs))

    def validate_certifications(self, job: Job, technician: Technician, db: Session = None) -> Dict[str, Any]:
        """
        Validate technician certifications against job requirements
        Returns qualification status with detailed reasons
        """
        req_str = job.required_skill or ""
        required = set(s.strip().upper() for s in req_str.split(",") if s.strip())
        
        tech_str = technician.technician_skill or ""
        held = set(s.strip().upper() for s in tech_str.split(",") if s.strip())
        
        if not required:
            return {"qualified": True}
            
        # Check direct skill match
        missing_direct = required - held
        if missing_direct:
            return {
                "qualified": False,
                "reason": "missing_skills",
                "details": list(missing_direct),
                "message": f"Missing skills: {', '.join(missing_direct)}"
            }
            
        # Check prerequisites (BR-005)
        for req_skill in required:
            prereqs = self.get_prerequisites(req_skill)
            for prereq in prereqs:
                if prereq not in held:
                    return {
                        "qualified": False,
                        "reason": "missing_prerequisite",
                        "for_skill": req_skill,
                        "missing_prerequisite": prereq,
                        "message": f"Missing prerequisite for {req_skill}: {prereq}"
                    }
                    
        # Check expiration dates
        date_checks = self.get_expired_and_expiring_certifications(technician)
        expired = date_checks["expired"]
        expiring_soon = date_checks["expiring_soon"]
        
        warnings = []
        
        if expired:
            needed_skills = required.copy()
            for r in required:
                needed_skills.update(self.get_prerequisites(r))
            
            expired_needed = [s for s in expired if s in needed_skills]
            if expired_needed:
                return {
                    "qualified": False,
                    "reason": "expired_certifications",
                    "details": expired_needed,
                    "message": f"Expired certifications: {', '.join(expired_needed)}"
                }
                
        if expiring_soon:
            needed_skills = required.copy()
            for r in required:
                needed_skills.update(self.get_prerequisites(r))
            expiring_needed = [s for s in expiring_soon if s in needed_skills]
            if expiring_needed:
                warnings.append(f"Certifications expiring soon (<30 days): {', '.join(expiring_needed)}")
                
        return {"qualified": True, "warnings": warnings}

    def log_disqualification(self, db: Session, job_id: int, technician: Technician, reason_data: Dict[str, Any]):
        """Create an immutable AuditEvent for rejection."""
        audit = AuditEvent(
            tech_id=technician.tech_id or f"id-{technician.technician_id}",
            tenant_id=technician.tenant_id or "default",
            event_type="CERT_REJECTED",
            old_status=reason_data.get("reason", "unknown")[:30],
            new_status="DISQUALIFIED"
        )
        db.add(audit)
