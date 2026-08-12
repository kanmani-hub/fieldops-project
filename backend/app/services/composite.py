from typing import List, Dict, Any

class CompositeScoringService:
    def get_weights(self, db, tenant_id: str) -> Dict[str, float]:
        """Fetch configurable weights from database (defaulting to 40-40-20)."""
        # In a real app, query TenantSettings table. For now, return default.
        return {"proximity": 0.4, "skill": 0.4, "workload": 0.2}
        
    def composite_score(self, prox: float, skill: float, work: float, weights: dict = None) -> Dict[str, Any]:
        """
        Calculate composite score with configurable weights and min-max normalization.
        """
        if weights is None:
            weights = {"proximity": 0.4, "skill": 0.4, "workload": 0.2}
            
        # Treat None as 0
        prox = prox if prox is not None else 0.0
        skill = skill if skill is not None else 0.0
        work = work if work is not None else 0.0
        
        # Normalize scores to 0-100 bounds
        p_norm = max(0.0, min(100.0, prox))
        s_norm = max(0.0, min(100.0, skill))
        w_norm = max(0.0, min(100.0, work))
        
        p_weighted = p_norm * weights.get("proximity", 0.4)
        s_weighted = s_norm * weights.get("skill", 0.4)
        w_weighted = w_norm * weights.get("workload", 0.2)
        
        composite = round(p_weighted + s_weighted + w_weighted, 2)
        
        return {
            "composite_score": composite,
            "breakdown": {
                "proximity": {"raw": prox, "weighted": round(p_weighted, 2)},
                "skill": {"raw": skill, "weighted": round(s_weighted, 2)},
                "workload": {"raw": work, "weighted": round(w_weighted, 2)}
            }
        }
        
    def rank_technicians(self, qualified: List[dict]) -> List[dict]:
        """
        Deterministic tie-breaking for equal composite scores:
        1. Composite Score (Highest first)
        2. Nearest distance (Lowest first)
        3. Fewer active jobs (Lowest first)
        4. Earlier registration (Lowest tech_id first) - fallback deterministic
        """
        def sort_key(x):
            # Composite score (negative for descending)
            c_score = -(x.get("composite_score") or 0.0)
            # Distance (ascending, default to 9999 if None to put them last)
            dist = x.get("distance_km")
            dist = dist if dist is not None else 9999.0
            # Active jobs (ascending)
            jobs = x.get("active_jobs", 9999)
            # Deterministic fallback (ascending string)
            t_id = str(x.get("tech_id", ""))
            
            return (c_score, dist, jobs, t_id)
            
        return sorted(qualified, key=sort_key)
