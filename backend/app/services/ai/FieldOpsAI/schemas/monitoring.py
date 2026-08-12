"""
monitoring.py

Pydantic schemas representing the Monitoring Agent contract.

The Monitoring Agent continuously evaluates
an active field service job and recommends
operational actions.

The Monitoring Agent NEVER:
- Updates the database
- Changes job status
- Sends notifications
- Dispatches technicians

It only returns structured AI recommendations.
"""

from typing import Literal

from pydantic import BaseModel, Field


# ------------------------------------------------------------------
# Job Information
# ------------------------------------------------------------------


class JobStatus(BaseModel):
    """
    Current job information.
    """

    job_id: int

    priority: Literal[
        "LOW",
        "MEDIUM",
        "HIGH",
        "CRITICAL",
    ]

    current_status: Literal[
        "ASSIGNED",
        "EN_ROUTE",
        "ON_SITE",
        "WORK_IN_PROGRESS",
    ]

    customer_location: str = Field(
        ...,
        description="Customer job location."
    )


# ------------------------------------------------------------------
# Technician Information
# ------------------------------------------------------------------


class TechnicianStatus(BaseModel):
    """
    Technician monitoring information.
    """

    technician_id: int

    technician_location: str = Field(
        ...,
        description="Current technician location."
    )

    customer_waiting: bool = Field(
        default=True,
        description="Whether customer is waiting."
    )

    traffic_delay: bool = Field(
        default=False,
        description="Whether traffic affects ETA."
    )


# ------------------------------------------------------------------
# SLA / ETA Information
# ------------------------------------------------------------------


class SLAStatus(BaseModel):
    """
    SLA and ETA metrics.
    """

    scheduled_eta_minutes: int = Field(
        ...,
        ge=0,
    )

    current_eta_minutes: int = Field(
        ...,
        ge=0,
    )

    elapsed_minutes: int = Field(
        ...,
        ge=0,
    )

    sla_remaining_minutes: int = Field(
        ...,
        description="Minutes remaining before SLA breach."
    )


# ------------------------------------------------------------------
# Monitoring Context
# ------------------------------------------------------------------


class MonitoringContext(BaseModel):
    """
    Complete Monitoring Agent context.
    """

    job: JobStatus

    technician: TechnicianStatus

    sla: SLAStatus


# ------------------------------------------------------------------
# AI Decision
# ------------------------------------------------------------------


class MonitoringDecision(BaseModel):
    """
    Structured recommendation returned
    by the Monitoring Agent.
    """

    action: Literal[
        "CONTINUE",
        "NOTIFY_CUSTOMER",
        "NOTIFY_DISPATCHER",
        "REQUEST_STATUS_UPDATE",
        "ESCALATE",
    ] = Field(
        ...,
        description="Recommended next action."
    )
    risk_level: Literal[
        "LOW",
        "MEDIUM",
        "HIGH",
        "CRITICAL",
    ] = Field(
        ...,
        description="Current operational risk."
    )

    notify_customer: bool = Field(
        ...,
        description="Whether customer notification is recommended."
    )

    notify_dispatcher: bool = Field(
        ...,
        description="Whether dispatcher notification is recommended."
    )

    escalation_required: bool = Field(
        ...,
        description="Whether escalation is recommende   d."
    )

    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
    )

    reason: str = Field(
        ...,
        min_length=10,
        description="Short explanation for the recommendation."
    )