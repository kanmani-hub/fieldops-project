"""
Role-Based Access Control (RBAC) system.

Five-tier hierarchy:
1. SUPER_ADMIN — Platform owner, cross-tenant access
2. ADMIN — Organization head, single tenant
3. DISPATCHER — Operational controller, single tenant
4. TECHNICIAN — Field worker, single tenant
5. CUSTOMER — End customer, single tenant

Authorization is always derived from the JWT — never from
request headers or payloads.
"""

from enum import Enum
from typing import Optional


class UserRole(str, Enum):
    """Enumeration of all platform roles."""
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    DISPATCHER = "dispatcher"
    TECHNICIAN = "technician"
    CUSTOMER = "customer"


class Permission(str, Enum):
    """Granular permissions for RBAC enforcement."""

    # Organization management
    ORG_CREATE = "org:create"
    ORG_MANAGE = "org:manage"
    ORG_VIEW_ALL = "org:view_all"
    ORG_SUSPEND = "org:suspend"
    ORG_DELETE = "org:delete"

    # User management
    USERS_CREATE = "users:create"
    USERS_MANAGE = "users:manage"
    USERS_VIEW = "users:view"
    USERS_DELETE = "users:delete"

    # Job management
    JOBS_CREATE = "jobs:create"
    JOBS_VIEW_ALL = "jobs:view_all"
    JOBS_VIEW_OWN = "jobs:view_own"
    JOBS_EDIT = "jobs:edit"
    JOBS_DELETE = "jobs:delete"
    JOBS_ASSIGN = "jobs:assign"
    JOBS_REASSIGN = "jobs:reassign"
    JOBS_CANCEL = "jobs:cancel"
    JOBS_ACCEPT_REJECT = "jobs:accept_reject"
    JOBS_STATUS_UPDATE = "jobs:status_update"

    # Technician management
    TECHNICIANS_CREATE = "technicians:create"
    TECHNICIANS_MANAGE = "technicians:manage"
    TECHNICIANS_VIEW_ALL = "technicians:view_all"
    TECHNICIANS_VIEW_OWN = "technicians:view_own"

    # Planning & Dispatch
    PLANNING_VIEW = "planning:view"
    PLANNING_MANAGE = "planning:manage"
    DISPATCH_MANAGE = "dispatch:manage"
    DISPATCH_QUEUE_VIEW = "dispatch:queue_view"

    # Dashboard
    DASHBOARD_VIEW = "dashboard:view"
    DASHBOARD_TECH_VIEW = "dashboard:tech_view"
    DASHBOARD_CUSTOMER_VIEW = "dashboard:customer_view"

    # Notifications
    NOTIFICATIONS_MANAGE = "notifications:manage"
    NOTIFICATIONS_VIEW_OWN = "notifications:view_own"
    NOTIFICATIONS_SEND = "notifications:send"

    # Templates
    TEMPLATES_MANAGE = "templates:manage"
    TEMPLATES_VIEW = "templates:view"

    # Audit
    AUDIT_VIEW = "audit:view"
    AUDIT_VIEW_SYSTEM = "audit:view_system"

    # Settings
    SETTINGS_MANAGE_ORG = "settings:manage_org"
    SETTINGS_MANAGE_GLOBAL = "settings:manage_global"

    # GPS & Tracking
    GPS_TRACK = "gps:track"
    GPS_TRACK_OWN = "gps:track_own"
    GPS_ADMIN = "gps:admin"

    # Customer management
    CUSTOMERS_MANAGE = "customers:manage"
    CUSTOMERS_VIEW_OWN = "customers:view_own"
    CUSTOMERS_CREATE_REQUEST = "customers:create_request"

    # Reports
    REPORTS_VIEW = "reports:view"
    REPORTS_DOWNLOAD = "reports:download"

    # Escalations
    ESCALATIONS_VIEW = "escalations:view"
    ESCALATIONS_MANAGE = "escalations:manage"

    # Platform (Super Admin only)
    PLATFORM_HEALTH = "platform:health"
    PLATFORM_ANALYTICS = "platform:analytics"


# ──────────────────────────────────────────────────
# Role → Permissions Matrix
# ──────────────────────────────────────────────────

ROLE_PERMISSIONS: dict[UserRole, set[Permission]] = {
    UserRole.SUPER_ADMIN: set(Permission),  # All permissions

    UserRole.ADMIN: {
        # User management within tenant
        Permission.USERS_CREATE,
        Permission.USERS_MANAGE,
        Permission.USERS_VIEW,
        Permission.USERS_DELETE,
        # Jobs
        Permission.JOBS_CREATE,
        Permission.JOBS_VIEW_ALL,
        Permission.JOBS_EDIT,
        Permission.JOBS_DELETE,
        Permission.JOBS_ASSIGN,
        Permission.JOBS_REASSIGN,
        Permission.JOBS_CANCEL,
        Permission.JOBS_STATUS_UPDATE,
        # Technicians
        Permission.TECHNICIANS_CREATE,
        Permission.TECHNICIANS_MANAGE,
        Permission.TECHNICIANS_VIEW_ALL,
        # Planning & Dispatch
        Permission.PLANNING_VIEW,
        Permission.PLANNING_MANAGE,
        Permission.DISPATCH_MANAGE,
        Permission.DISPATCH_QUEUE_VIEW,
        # Dashboard
        Permission.DASHBOARD_VIEW,
        # Notifications
        Permission.NOTIFICATIONS_MANAGE,
        Permission.NOTIFICATIONS_SEND,
        # Templates
        Permission.TEMPLATES_MANAGE,
        Permission.TEMPLATES_VIEW,
        # Audit (own tenant only)
        Permission.AUDIT_VIEW,
        # Settings (own org)
        Permission.SETTINGS_MANAGE_ORG,
        # GPS
        Permission.GPS_TRACK,
        Permission.GPS_ADMIN,
        # Customers
        Permission.CUSTOMERS_MANAGE,
        # Reports
        Permission.REPORTS_VIEW,
        Permission.REPORTS_DOWNLOAD,
        # Escalations
        Permission.ESCALATIONS_VIEW,
        Permission.ESCALATIONS_MANAGE,
    },

    UserRole.DISPATCHER: {
        # Jobs
        Permission.JOBS_CREATE,
        Permission.JOBS_VIEW_ALL,
        Permission.JOBS_EDIT,
        Permission.JOBS_ASSIGN,
        Permission.JOBS_REASSIGN,
        Permission.JOBS_CANCEL,
        Permission.JOBS_STATUS_UPDATE,
        # Technicians
        Permission.TECHNICIANS_CREATE,
        Permission.TECHNICIANS_MANAGE,
        Permission.TECHNICIANS_VIEW_ALL,
        # Planning & Dispatch
        Permission.PLANNING_VIEW,
        Permission.PLANNING_MANAGE,
        Permission.DISPATCH_MANAGE,
        Permission.DISPATCH_QUEUE_VIEW,
        # Dashboard
        Permission.DASHBOARD_VIEW,
        # Notifications
        Permission.NOTIFICATIONS_MANAGE,
        Permission.NOTIFICATIONS_SEND,
        # Templates
        Permission.TEMPLATES_VIEW,
        # GPS
        Permission.GPS_TRACK,
        # Customers
        Permission.CUSTOMERS_MANAGE,
        # Reports
        Permission.REPORTS_VIEW,
        # Escalations
        Permission.ESCALATIONS_VIEW,
        Permission.ESCALATIONS_MANAGE,
        # Users (view only)
        Permission.USERS_VIEW,
    },

    UserRole.TECHNICIAN: {
        # Own jobs only
        Permission.JOBS_VIEW_OWN,
        Permission.JOBS_ACCEPT_REJECT,
        Permission.JOBS_STATUS_UPDATE,
        # Own profile
        Permission.TECHNICIANS_VIEW_OWN,
        # Dashboard
        Permission.DASHBOARD_TECH_VIEW,
        # Own notifications
        Permission.NOTIFICATIONS_VIEW_OWN,
        # GPS (own location)
        Permission.GPS_TRACK_OWN,
    },

    UserRole.CUSTOMER: {
        # Own jobs / service requests
        Permission.JOBS_VIEW_OWN,
        Permission.CUSTOMERS_CREATE_REQUEST,
        Permission.CUSTOMERS_VIEW_OWN,
        # Dashboard
        Permission.DASHBOARD_CUSTOMER_VIEW,
        # Notifications
        Permission.NOTIFICATIONS_VIEW_OWN,
        # GPS (track assigned technician)
        Permission.GPS_TRACK_OWN,
        # Reports
        Permission.REPORTS_DOWNLOAD,
    },
}


def has_permission(role: UserRole, permission: Permission) -> bool:
    """Check if a role has a specific permission."""
    perms = ROLE_PERMISSIONS.get(role, set())
    return permission in perms


def get_permissions(role: UserRole) -> set[Permission]:
    """Get all permissions for a role."""
    return ROLE_PERMISSIONS.get(role, set())


def is_super_admin(role: str) -> bool:
    """Check if the role string represents a super admin."""
    return role == UserRole.SUPER_ADMIN.value


def role_hierarchy_level(role: UserRole) -> int:
    """
    Return the hierarchy level of a role.
    Higher number = more authority.
    """
    hierarchy = {
        UserRole.CUSTOMER: 1,
        UserRole.TECHNICIAN: 2,
        UserRole.DISPATCHER: 3,
        UserRole.ADMIN: 4,
        UserRole.SUPER_ADMIN: 5,
    }
    return hierarchy.get(role, 0)


def can_manage_role(manager_role: UserRole, target_role: UserRole) -> bool:
    """
    Check if a manager role can manage (create/edit/delete) a target role.
    
    A role can only manage roles strictly below it in the hierarchy.
    Super Admin can manage everyone.
    """
    if manager_role == UserRole.SUPER_ADMIN:
        return True
    return role_hierarchy_level(manager_role) > role_hierarchy_level(target_role)
