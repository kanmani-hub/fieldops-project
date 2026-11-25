
"""
CustomerProfileModel model.

Stores extended profile information for customer users.
Links 1:1 to the User model via user_id.
Tracks profile completion status for first-login flow.
"""

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    String,
    Text,
    ForeignKey,
    Index,
)
from sqlalchemy.sql import func

from ..database import Base


class CustomerProfileModel(Base):
    """Extended profile for customer users."""

    __tablename__ = "customer_profiles_extended"

    id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Personal information
    full_name = Column(String(200), nullable=False)
    mobile_number = Column(String(20), nullable=False)

    # Address
    address = Column(Text, nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    pincode = Column(String(10), nullable=True)

    # Optional company
    company_name = Column(String(200), nullable=True)

    # Profile status
    profile_completed = Column(
        Boolean,
        nullable=False,
        default=False,
    )

    # Timestamps
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("idx_cust_profile_user", "user_id"),
    )
