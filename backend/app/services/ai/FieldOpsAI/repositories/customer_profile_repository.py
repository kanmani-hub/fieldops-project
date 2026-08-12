from sqlalchemy.orm import Session
from app.models import CustomerProfile, CustomerPreferenceAudit
from typing import Optional, List

class CustomerProfileRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_customer(
        self,
        tenant_id: str,
        customer_id: str,
        *,
        for_update: bool = False
    ) -> Optional[CustomerProfile]:
        query = self.db.query(CustomerProfile).filter(
            CustomerProfile.tenant_id == tenant_id,
            CustomerProfile.customer_id == customer_id
        )
        
        if for_update:
            # Conditionally use with_for_update for postgres, skip for sqlite to keep tests compatible.
            engine = self.db.get_bind()
            if engine.dialect.name != "sqlite":
                query = query.with_for_update()
                
        return query.first()

    def add_profile(self, profile: CustomerProfile) -> CustomerProfile:
        self.db.add(profile)
        self.db.flush()
        return profile

    def add_audit(self, audit: CustomerPreferenceAudit) -> CustomerPreferenceAudit:
        self.db.add(audit)
        self.db.flush()
        return audit

    def list_audits(self, tenant_id: str, profile_id: str) -> List[CustomerPreferenceAudit]:
        return self.db.query(CustomerPreferenceAudit).filter(
            CustomerPreferenceAudit.tenant_id == tenant_id,
            CustomerPreferenceAudit.customer_profile_id == profile_id
        ).order_by(CustomerPreferenceAudit.created_at.asc()).all()
