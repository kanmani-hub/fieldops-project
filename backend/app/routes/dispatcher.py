from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from ..auth.dependencies import get_current_user, AuthenticatedUser, require_permission
from ..auth.rbac import Permission


router = APIRouter(
    prefix="/dispatchers",
    tags=["Dispatchers"],
)


class DispatcherActiveUpdate(BaseModel):
    is_active: bool


@router.get("")
async def get_dispatchers(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get dispatchers belonging ONLY to the current authenticated user's tenant.
    """

    # Get tenant from the authenticated JWT user
    tenant_id = current_user.tenant_id

    dispatchers = (
        db.query(
            models.User,
            models.Organization.name.label("organization_name"),
        )
        .outerjoin(
            models.Organization,
            models.Organization.id == models.User.tenant_id,
        )
        .filter(
            models.User.tenant_id == tenant_id,
            models.User.role == "dispatcher",
            models.User.deleted_at.is_(None),
        )
        .order_by(
            models.User.first_name,
            models.User.last_name,
        )
        .all()
    )

    return [
        {
            "id": user.id,
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "name": f"{user.first_name} {user.last_name}".strip(),
            "role": user.role,
            "tenant_id": user.tenant_id,
            "organization_name": organization_name,
            "is_active": user.is_active,
            "is_on_duty": user.is_on_duty,
            "phone_number": user.phone_number,
            "last_login": user.last_login,
            "created_at": user.created_at,
            "updated_at": user.updated_at,
        }
        for user, organization_name in dispatchers
    ]


@router.patch("/{dispatcher_id}/active")
async def update_dispatcher_active_status(
    dispatcher_id: str,
    payload: DispatcherActiveUpdate,
    current_user: AuthenticatedUser = Depends(
        require_permission(Permission.USERS_MANAGE)
    ),
    db: Session = Depends(get_db),
):
    """Deactivate or reactivate a dispatcher in the current organization."""
    organization = (
        db.query(models.Organization)
        .filter(models.Organization.id == current_user.tenant_id)
        .with_for_update()
        .first()
    )
    if organization is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    dispatcher = (
        db.query(models.User)
        .filter(
            models.User.id == dispatcher_id,
            models.User.tenant_id == current_user.tenant_id,
            models.User.role == "dispatcher",
            models.User.deleted_at.is_(None),
        )
        .first()
    )
    if dispatcher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dispatcher not found",
        )

    if payload.is_active:
        other_active_dispatcher = (
            db.query(models.User.id)
            .filter(
                models.User.tenant_id == current_user.tenant_id,
                models.User.role == "dispatcher",
                models.User.is_active.is_(True),
                models.User.deleted_at.is_(None),
                models.User.id != dispatcher.id,
            )
            .first()
        )
        if other_active_dispatcher:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This organization already has an active dispatcher. Deactivate the current dispatcher before reactivating this one.",
            )

    dispatcher.is_active = payload.is_active
    if not payload.is_active:
        dispatcher.is_on_duty = False

    db.commit()
    db.refresh(dispatcher)
    return {
        "message": "Dispatcher activated" if dispatcher.is_active else "Dispatcher deactivated",
        "id": dispatcher.id,
        "is_active": dispatcher.is_active,
        "is_on_duty": dispatcher.is_on_duty,
    }
