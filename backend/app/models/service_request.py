"""
ServiceRequest model.

Represents a customer's service request that may be linked
to a Job once assigned by a dispatcher.
"""

import uuid
from sqlalchemy import (
    Column, DateTime, Date, Integer, String, Text,
    ForeignKey, Index, JSON,
)
from sqlalchemy.sql import func

from ..database import Base
from sqlalchemy.orm import relationship

from app.models import Organization


class ServiceRequest(Base):
    """
    Customer service request.

    Lifecycle: PENDING → ASSIGNED → IN_PROGRESS → COMPLETED
                                                 → CANCELLED

    Once a dispatcher creates a Job from this request, `linked_job_id`
    is populated.
    """
    __tablename__ = "service_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    request_number = Column(String(50), unique=True, index=True, nullable=False)
    customer_user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id = Column(String(50),ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True)

    # Request details
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)
    service_type = Column(String(100), nullable=True)
    priority = Column(String(20), nullable=False, default="MEDIUM")
    preferred_visit_date = Column(Date, nullable=True)
    images = Column(JSON, nullable=True, default=list)

    # Address / location
    location = Column(String(255), nullable=True)
    contact_number = Column(String(20), nullable=True)

    # Status tracking
    status = Column(String(30), nullable=False, default="PENDING")
    # PENDING, ASSIGNED, IN_PROGRESS, COMPLETED, CANCELLED

    # Link to the job created from this request
    linked_job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True, index=True)

    # Cancellation
    cancellation_reason = Column(Text, nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_sr_customer", "customer_user_id"),
        Index("idx_sr_tenant_status", "tenant_id", "status"),
        Index("idx_sr_linked_job", "linked_job_id"),
    )

    organization=relationship("Organization",back_populates="service_requests")
