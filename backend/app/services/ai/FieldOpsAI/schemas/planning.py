"""
planning.py

Pydantic schema representing the Planning Agent decision.

The Planning Agent is responsible for:

- Evaluating technician candidates.
- Ranking technician recommendations.
- Returning up to three recommended technicians.
"""

from typing import Literal, Optional, Any, Dict, List

from pydantic import BaseModel, Field

class RecommendedTechnician(BaseModel):
    technician_id: int = Field(
        ...,
        description="Recommended technician identifier."
    )

    rank: int = Field(
        ...,
        ge=1,
        le=3,#Top 3 recommendations only
        description="Recommendation rank."
    )

    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score for this recommendation."
    )

    estimated_eta:int = Field(
        ...,
        ge=0,
        description="Estimated arrival time in minutes."
    )



class PlanningDecision(BaseModel):
    """
    Structured AI decision returned by the Planning Agent.
    """

    action: Literal[
        "assign_technician",
        "manual_review",
        "no_assignment",
    ] = Field(
        ...,
        description="Planning decision."
    )

    job_id:Optional[int] = Field(
        default=None,
        description="Job identifier."
    )

    recommended_technicians: List[RecommendedTechnician] = Field(
        default_factory=list,
        max_length=3,
        description="Ranked technician recommendations."
    )

    priority: Literal[
        "LOW",
        "MEDIUM",   
        "HIGH",
        "CRITICAL",
    ] = Field(
        ...,
        description="Job priority."
    )

    reason: str = Field(
        ...,
        min_length=10,
        description="Business explanation for the recommendation."
    )

    

class PlanningContext(BaseModel):
    job_id: Optional[int] = None
    customer_request: Dict
    available_technicians: List[Dict[str, Any]]
    rejected_technician_ids: List[int] = Field(
        default_factory=list,
        description="Technicians who have already rejected or timed out for this job."
    )