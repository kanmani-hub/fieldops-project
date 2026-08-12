"""
brand_safety_admin.py

Protected FastAPI routes for tenant-specific AI brand-safety
rule administration.

Responsibilities
----------------
- Require authentication
- Require an approved administration role
- Require a trusted tenant ID
- Validate request and response schemas
- Call BrandSafetyAdminService
- Translate service errors into HTTP responses

The route never queries AIBrandSafetyRule directly.
"""

from __future__ import annotations

from typing import Annotated,NoReturn

from fastapi import APIRouter,Depends,HTTPException,Query,status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.brand_safety_authorization import BrandSafetyAdminPrincipal,get_trusted_tenant_id,require_brand_safety_admin

from app.redis_client import RedisCacheManager,get_redis_client
from app.services.ai.guardrails.brand_safety_admin_schemas import BrandSafetyRuleCreate,BrandSafetyRuleResponse,BrandSafetyRuleUpdate
from app.services.ai.guardrails.brand_safety_admin_service import (
    BrandSafetyAdminError,
    BrandSafetyAdminService,
    BrandSafetyRuleConflictError,
    BrandSafetyRuleNotFoundError,
    BrandSafetyRulePersistenceError,
)
from app.services.ai.guardrails.brand_safety_validator import (
    BrandSafetyRuleCategory,
)


router = APIRouter(
    prefix="/api/v1/admin/ai/brand-safety-rules",
    tags=["AI Brand Safety Administration"],
)


def get_brand_safety_admin_service(
    tenant_id: Annotated[
        str,
        Depends(get_trusted_tenant_id),
    ],
    principal: Annotated[
        BrandSafetyAdminPrincipal,
        Depends(require_brand_safety_admin),
    ],
    db: Annotated[
        Session,
        Depends(get_db),
    ],
    redis_client: Annotated[
        RedisCacheManager,
        Depends(get_redis_client),
    ],
) -> BrandSafetyAdminService:
    """
    Build one tenant-scoped administration service.

    Tenant and actor information come from trusted request
    dependencies, not from the request body.
    """

    return BrandSafetyAdminService(
        db=db,
        tenant_id=tenant_id,
        actor_id=principal.actor_id,
        redis_client=redis_client,
    )


def raise_admin_http_error(
    exc: BrandSafetyAdminError,
) -> NoReturn:
    """
    Translate service-layer errors into safe HTTP responses.
    """

    if isinstance(
        exc,
        BrandSafetyRuleNotFoundError,
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    if isinstance(
        exc,
        BrandSafetyRuleConflictError,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    if isinstance(
        exc,
        BrandSafetyRulePersistenceError,
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Brand-safety configuration is temporarily "
                "unavailable."
            ),
        ) from exc

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Brand-safety administration failed.",
    ) from exc


@router.post(
    "",
    response_model=BrandSafetyRuleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a brand-safety rule",
)
def create_brand_safety_rule(
    payload: BrandSafetyRuleCreate,
    service: Annotated[
        BrandSafetyAdminService,
        Depends(get_brand_safety_admin_service),
    ],
):
    """
    Create one rule for the authenticated tenant.
    """

    try:
        return service.create_rule(
            payload
        )

    except BrandSafetyAdminError as exc:
        raise_admin_http_error(
            exc
        )


@router.get(
    "",
    response_model=list[BrandSafetyRuleResponse],
    summary="List brand-safety rules",
)
def list_brand_safety_rules(
    service: Annotated[
        BrandSafetyAdminService,
        Depends(get_brand_safety_admin_service),
    ],
    active: Annotated[
        bool | None,
        Query(
            description=(
                "true returns active rules, false returns "
                "inactive rules, and omission returns both."
            ),
        ),
    ] = None,
    category: Annotated[
        BrandSafetyRuleCategory | None,
        Query(
            description="Optional rule-category filter.",
        ),
    ] = None,
):
    """
    List persisted rules belonging only to the authenticated
    tenant.
    """

    try:
        return service.list_rules(
            active_only=active,
            category=category,
        )

    except BrandSafetyAdminError as exc:
        raise_admin_http_error(
            exc
        )


@router.get(
    "/{rule_id}",
    response_model=BrandSafetyRuleResponse,
    summary="Get one brand-safety rule",
)
def get_brand_safety_rule(
    rule_id: str,
    service: Annotated[
        BrandSafetyAdminService,
        Depends(get_brand_safety_admin_service),
    ],
):
    """
    Retrieve one tenant-specific rule.
    """

    try:
        return service.get_rule(
            rule_id
        )

    except BrandSafetyAdminError as exc:
        raise_admin_http_error(
            exc
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.patch(
    "/{rule_id}",
    response_model=BrandSafetyRuleResponse,
    summary="Update a brand-safety rule",
)
def update_brand_safety_rule(
    rule_id: str,
    payload: BrandSafetyRuleUpdate,
    service: Annotated[
        BrandSafetyAdminService,
        Depends(get_brand_safety_admin_service),
    ],
):
    """
    Update selected fields on one tenant-specific rule.
    """

    try:
        return service.update_rule(
            rule_id=rule_id,
            payload=payload,
        )

    except BrandSafetyAdminError as exc:
        raise_admin_http_error(
            exc
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post(
    "/{rule_id}/deactivate",
    response_model=BrandSafetyRuleResponse,
    summary="Deactivate a brand-safety rule",
)
def deactivate_brand_safety_rule(
    rule_id: str,
    service: Annotated[
        BrandSafetyAdminService,
        Depends(get_brand_safety_admin_service),
    ],
):
    """
    Disable a rule without deleting its database record.
    """

    try:
        return service.deactivate_rule(
            rule_id
        )

    except BrandSafetyAdminError as exc:
        raise_admin_http_error(
            exc
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc