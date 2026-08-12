"""
Seed default organizations and users for demo & testing.

Created users:
1. Super Admin: superhead@fieldops.com / SuperAdmin@123
2. Org Admin:   admin@fieldops.com / Admin@123456
3. Dispatcher:  dispatcher@fieldops.com / Dispatcher@123456
4. Technician:  tech@fieldops.com / Tech@123456
5. Customer:    elastaff@gmail.com / Elastaff@123456
6. Customer:    customer@fieldops.com / Customer@123456
"""

import logging
from sqlalchemy.orm import Session
from .database import engine, Base
from .models.organization import Organization
from .models.user import User
from .auth.password import hash_password
from .auth.rbac import UserRole

logger = logging.getLogger(__name__)


def seed_organizations_and_users(db: Session):
    """Create initial database tables and seed default orgs and users."""
    try:
        # Create all tables if they don't exist yet
        Base.metadata.create_all(bind=engine)
    except Exception as e:
        logger.warning(f"Metadata create_all warning: {e}")

    # 1. Seed Default Organization: tenant-1
    default_org = db.query(Organization).filter(Organization.id == "tenant-1").first()
    if not default_org:
        default_org = Organization(
            id="tenant-1",
            name="FieldOps Core Enterprise",
            slug="fieldops-core",
            status="ACTIVE",
            subscription_plan="ENTERPRISE",
            max_users=100,
            max_technicians=500,
            contact_email="support@fieldops.com",
        )
        db.add(default_org)
        db.flush()
        logger.info("Seeded default organization: tenant-1")

    # 2. Seed System Organization for Super Admin
    system_org = db.query(Organization).filter(Organization.id == "__platform__").first()
    if not system_org:
        system_org = Organization(
            id="__platform__",
            name="Platform Administration",
            slug="platform-admin",
            status="ACTIVE",
            subscription_plan="ENTERPRISE",
            max_users=9999,
            max_technicians=9999,
            contact_email="superadmin@fieldops.com",
        )
        db.add(system_org)
        db.flush()
        logger.info("Seeded platform organization: __platform__")

    # List of default users to seed
    seed_users_data = [
        {
            "email": "superhead@fieldops.com",
            "password": "SuperAdmin@123",
            "first_name": "Super",
            "last_name": "Admin",
            "role": UserRole.SUPER_ADMIN.value,
            "tenant_id": "__platform__",
        },
        {
            "email": "admin@fieldops.com",
            "password": "Admin@123456",
            "first_name": "Rajesh",
            "last_name": "Admin",
            "role": UserRole.ADMIN.value,
            "tenant_id": "tenant-1",
        },
        {
            "email": "dispatcher@fieldops.com",
            "password": "Dispatcher@123456",
            "first_name": "David",
            "last_name": "Dispatcher",
            "role": UserRole.DISPATCHER.value,
            "tenant_id": "tenant-1",
        },
        {
            "email": "tech@fieldops.com",
            "password": "Tech@123456",
            "first_name": "Tom",
            "last_name": "Technician",
            "role": UserRole.TECHNICIAN.value,
            "tenant_id": "tenant-1",
        },
        {
            "email": "elastaff@gmail.com",
            "password": "Elastaff@123456",
            "first_name": "Ela",
            "last_name": "Staff",
            "role": UserRole.ADMIN.value,
            "tenant_id": "tenant-1",
        },
        {
            "email": "customer@fieldops.com",
            "password": "Customer@123456",
            "first_name": "Carl",
            "last_name": "Customer",
            "role": UserRole.CUSTOMER.value,
            "tenant_id": "tenant-1",
        },
    ]
    for udata in seed_users_data:
        existing = db.query(User).filter(
            User.email == udata["email"],
            User.tenant_id == udata["tenant_id"],
            User.deleted_at.is_(None),
        ).first()

        if not existing:
            user = User(
                email=udata["email"],
                password_hash=hash_password(udata["password"]),
                first_name=udata["first_name"],
                last_name=udata["last_name"],
                role=udata["role"],
                tenant_id=udata["tenant_id"],
                is_active=True,
                is_email_verified=True,
            )
            db.add(user)
            logger.info("Seeded default user: %s (%s)", udata["email"], udata["role"])

    db.commit()


if __name__ == "__main__":
    from .database import SessionLocal
    db = SessionLocal()
    try:
        seed_organizations_and_users(db)
        print("Seed completed successfully!")
    finally:
        db.close()



