"""
Models package.

Re-exports all models from the original models_legacy.py
(renamed from models.py) plus the new multi-tenant models.

All existing `from app.models import X` imports continue to work.
"""

# Original models — backward compatible re-export
# This imports everything from the renamed models_legacy.py
from ..models_legacy import (  # noqa: F401
    Base,
    Tenant,
    Technician,
    Job,
    AuditEvent,
    DispatcherNotification,
    NotificationDelivery,
    SMSDelivery,
    InAppNotification,
    NotificationTemplate,
    TemplateVersion,
    PreferenceAuditLog,
    SkillTaxonomy,
    ScoringConfiguration,
    AIBrandSafetyRule,
    SLAEscalation,
    DispatcherAlert,
    OverrideAuditEvent,
    AIGuardrailViolation,
    AssignmentOverride,
    GPSPing,
    TenantGPSConfiguration,
    GPSPurgeAuditLog,
    GPSRejectedPingLog,
    CommunicationChannelConfiguration,
    CommunicationConfigurationAudit,
    ETAHistory,
    SecurityAuditLog,
    JobAssignment,
    AgentStateRecord,
    CustomerProfile,
    CustomerPreferenceAudit,
)

# New multi-tenant & job closure models
from .user import User, RefreshToken  # noqa: F401
from .organization import Organization  # noqa: F401
from .enterprise_audit import EnterpriseAuditLog  # noqa: F401
from .job_closure import JobClosure  # noqa: F401

# Portal models
from .technician_profile import TechnicianProfile  # noqa: F401
from .customer_profile import CustomerProfileModel  # noqa: F401
from .service_request import ServiceRequest  # noqa: F401

