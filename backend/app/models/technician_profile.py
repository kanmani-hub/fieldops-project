"""
TechnicianProfile model.

Stores extended profile information for technician users.
Links 1:1 to the User model via user_id.
Tracks profile completion status for first-login flow.
"""

import uuid
from sqlalchemy import (
    Boolean, Column, DateTime, Date, Float, Integer, String, Text,
    ForeignKey, Index, JSON,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..database import Base


class TechnicianProfile(Base):
    """
    Extended profile for technician users.

    Created when a technician completes their profile for the first time.
    The `profile_completed` flag drives the "Complete Your Profile" vs
    "Edit Profile" UI flow.
    """
    __tablename__ = "technician_profiles"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    tenant_id = Column(String(50),ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True)

    # Personal info
    full_name = Column(String(200), nullable=False)
    profile_photo = Column(Text, nullable=True)  # base64 or URL
    mobile_number = Column(String(20), nullable=False)
    date_of_birth = Column(Date, nullable=True)
    gender = Column(String(20), nullable=True)

    # Address
    address = Column(Text, nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    pincode = Column(String(10), nullable=True)

    # Emergency contact
    emergency_contact = Column(String(100), nullable=True)

    # Professional info
    skills = Column(JSON, nullable=True, default=list)
    experience = Column(String(200), nullable=True)
    certifications = Column(JSON, nullable=True, default=list)

    # Profile status
    profile_completed = Column(Boolean, nullable=False, default=False)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    organization=relationship("Organization",back_populates="technician_profile")

    __table_args__ = (
        Index("idx_tech_profile_tenant", "tenant_id"),
        Index("idx_tech_profile_user", "user_id"),
    )
