"""
dispatch.py

Pydantic schemas representing the Dispatch Agent decision.

The Dispatch Agent is responsible for:

- Analyzing technician responses.
- Recommending the next workflow action.
- Determining whether the backend should:
    - Continue with the accepted technician,
    - Assign the next ranked technician, or
    - Request a new planning recommendation.

The Dispatch Agent NEVER:

- Assigns technicians.
- Updates the database.
- Changes job status.
- Sends notifications.
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class DispatchDecision(BaseModel):
    """
    Structured AI decision returned by the Dispatch Agent.
    """

    action: Literal[
        "complete_assignment",
        "assign_next_candidate",
        "request_replanning",
        "manual_review",
    ] = Field(
        ...,
        description="Workflow action recommended by the AI."
    )

    job_id: int = Field(
        ...,
        description="Unique job identifier."
    )

    technician_id:Optional[int]  = Field(
        ...,
        description="Technician who generated the dispatch event."
    )

    status: Literal[
        "ACCEPTED",
        "REJECTED",
        "TIMEOUT",
    ] = Field(
        ...,
        description="Technician response status."
    )

    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="AI confidence score."
    )

    reason: str = Field(
        ...,
        min_length=10,
        description="Business explanation for the workflow recommendation."
    )


class DispatchContext(BaseModel):
    """
    Context provided to the Dispatch Agent.
    """

    job: Dict[str, Any]

    current_technician: Dict[str, Any]

    event: Literal[
        "TECHNICIAN_ACCEPTED",
        "TECHNICIAN_REJECTED",
        "TECHNICIAN_TIMEOUT",
    ]

    remaining_candidates: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Remaining ranked technicians available for assignment."
    )

    rejected_technician_ids: List[int] = Field(
        default_factory=list,
        description="Technicians who have already rejected or timed out for this job."
    )