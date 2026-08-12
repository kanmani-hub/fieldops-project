"""
closure.py

Pydantic schemas representing the Closure Agent.

The Closure Agent generates structured information
after a technician completes a job.

The backend decides how to use the generated content.

The Closure Agent NEVER:
- Updates the database
- Changes job status
- Sends notifications
- Makes business decisions
"""

from typing import List, Optional

from pydantic import BaseModel, Field


# ==========================================================
# Context
# ==========================================================


class ClosureContext(BaseModel):
    """
    Context provided to the Closure Agent.
    """

    customer_name: Optional[str] = Field(
        default=None,
        description="Customer name."
    )

    technician_name: Optional[str] = Field(
        default=None,
        description="Technician who completed the work."
    )

    job_type: str = Field(
        ...,
        description="Type of service performed."
    )

    technician_notes: str = Field(
        ...,
        min_length=5,
        description="Technician's completion notes."
    )

    parts_used: List[str] = Field(
        default_factory=list,
        description="Parts replaced or installed."
    )

    duration_minutes: int = Field(
        ...,
        ge=0,
        description="Work duration in minutes."
    )

    customer_confirmation: Optional[str] = Field(
        default=None,
        description="Optional customer confirmation."
    )


# ==========================================================
# AI Decision
# ==========================================================


class ClosureDecision(BaseModel):
    """
    Structured AI-generated closure information.
    """

    work_summary: str = Field(
        ...,
        min_length=10,
        description="Internal summary of completed work."
    )

    customer_summary: str = Field(
        ...,
        min_length=10,
        description="Customer-friendly completion summary."
    )

    invoice_description: str = Field(
        ...,
        min_length=5,
        description="Invoice description."
    )

    follow_up_required: bool = Field(
        ...,
        description="Whether follow-up is recommended."
    )

    follow_up_reason: Optional[str] = Field(
        default=None,
        description="Reason for follow-up."
    )

    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="AI confidence score."
    )