from typing import List, Dict, Any, Set
import json
import logging
from app.redis_client import get_redis_client

logger = logging.getLogger("fieldops")

# Default taxonomy mapping if DB/Redis is empty
DEFAULT_TAXONOMY = {
    "HVAC_CERT": {
        "prerequisites": ["ELECTRICAL_LV"],
        "equivalents": ["HVAC_ADVANCED"]
    },
    "ELECTRICAL_HV": {
        "prerequisites": ["ELECTRICAL_LV"],
        "equivalents": []
    },
    "AC MECHANIC": {
        "prerequisites": [],
        "equivalents": ["HVAC_CERT"]
    }
}

class SkillScoringService:
    def get_taxonomy(self, db=None) -> Dict[str, Any]:
        """Fetch skill taxonomy, caching in Redis for 60s."""
        redis_client = get_redis_client()
        cache_key = "skill_taxonomy"
        
        cached = redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
            
        # In a real app, you would query a SkillTaxonomy DB table here.
        # For now, we use the default dictionary.
        taxonomy = DEFAULT_TAXONOMY
        
        redis_client.setex(cache_key, 60, json.dumps(taxonomy))
        return taxonomy
        
    def expand_equivalents(self, skills: Set[str], taxonomy: Dict[str, Any]) -> Set[str]:
        """Expands technician skills to include all configured equivalents."""
        expanded = set(skills)
        for held_skill in skills:
            # Check if this skill is an equivalent for any parent skill in taxonomy
            for tax_skill, data in taxonomy.items():
                equivalents = [e.upper() for e in data.get("equivalents", [])]
                if held_skill in equivalents:
                    expanded.add(tax_skill.upper())
        return expanded
        
    def calculate_skill_score(self, req_str: str, tech_str: str, db, service_type: str = "") -> Dict[str, Any]:
        """
        Calculate skill match score (0-100) with prerequisite validation.
        """
        from app.utils import is_skill_matching
        
        if is_skill_matching(tech_str, req_str, service_type):
            return {
                "score": 100.0,
                "qualified": True,
                "matched_skills": [tech_str] if tech_str else [],
                "missing_skills": []
            }
        else:
            return {
                "score": 0.0,
                "qualified": False,
                "reason": f"Skill mismatch: Technician provides '{tech_str}' but job requires '{req_str or service_type}'",
                "matched_skills": [],
                "missing_skills": [req_str] if req_str else []
            }
