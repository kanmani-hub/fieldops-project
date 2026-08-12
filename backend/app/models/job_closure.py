"""
JobClosure model for storing post-completion details, costs, and images.
"""

from sqlalchemy import Column, Integer, String, Text, Float, JSON, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from ..models_legacy import Base


class JobClosure(Base):
    __tablename__ = "job_closures"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False, index=True)
    tenant_id = Column(String(50),ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True)
    work_summary = Column(Text, nullable=False)
    before_images = Column(JSON, nullable=True)
    after_images = Column(JSON, nullable=False)
    labour_cost = Column(Float, nullable=False, default=0.0)
    material_cost = Column(Float, nullable=False, default=0.0)
    subtotal = Column(Float, nullable=False, default=0.0)
    completed_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    organization=relationship("Organization",back_populates="job_closures")

    job = relationship("Job", backref="closure")
