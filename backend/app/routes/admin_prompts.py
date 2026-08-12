from __future__ import annotations

import re
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Query,
    Response,
    status,
)
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.prompt_admin_authorization import (
    PromptAdminPrincipal,
    require_prompt_admin,
)
from app.redis_client import get_redis_client
from app.services.ai.FieldOpsAI.schemas.prompt_template import (
    AgentType,
    PromptChannel,
    PromptLanguage,
    PromptTemplateCreate,
    PromptTemplateLookupResponse,
    PromptTemplateResponse,
    PromptTemplateUpdate,
)
from app.services.ai.FieldOpsAI.services.managed_prompt_template_registry import (
    ConflictError,
    ManagedPromptTemplateRegistry,
    NotFoundError,
    RegistryServiceError,
    TemplateValidationServiceError,
)
from app.services.template_version_service import (
    TemplateNotFoundError,
    VersionNotFoundError,
    ConflictError as VersionConflictError,
    TemplateVersionError
)


router = APIRouter(
    prefix="/admin/prompts",
    tags=["Admin Prompts"],
)


# ==========================================================
# Validation helpers
# ==========================================================


def normalize_status(
    value: Optional[str],
) -> Optional[str]:
    """
    Normalize a prompt status into a canonical status string.

    Examples:
        ASSIGNED -> assigned
        en_route -> enroute
        ON-SITE -> onsite
        canceled -> cancelled

    Blank or unsupported status values are rejected with HTTP 400.
    """
    if value is None:
        return None

    try:
        from app.services.ai.FieldOpsAI.schemas.prompt_template import (
            normalize_template_status,
            UnsupportedTemplateStatusError,
        )
        res = normalize_template_status(value, allow_default=True)
        return res.value if hasattr(res, "value") else str(res)
    except UnsupportedTemplateStatusError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Template validation failed.",
        ) from None


# ==========================================================
# Dependencies
# ==========================================================


def get_registry(
    principal: PromptAdminPrincipal = Depends(
        require_prompt_admin
    ),
    db: Session = Depends(get_db),
    redis_client=Depends(get_redis_client),
) -> ManagedPromptTemplateRegistry:
    """
    Build a tenant-scoped registry for the authenticated actor.
    """

    return ManagedPromptTemplateRegistry(
        db=db,
        tenant_id=principal.tenant_id,
        actor_id=principal.actor_id,
        redis_client=redis_client,
    )


# ==========================================================
# Create
# ==========================================================


@router.post(
    "",
    response_model=PromptTemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_prompt(
    payload: PromptTemplateCreate,
    registry: ManagedPromptTemplateRegistry = Depends(
        get_registry
    ),
) -> PromptTemplateResponse:
    try:
        return registry.create(payload)

    except ConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "An active template with this "
                "configuration already exists."
            ),
        ) from None

    except TemplateValidationServiceError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Template validation failed.",
        ) from None

    except RegistryServiceError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Prompt registry unavailable.",
        ) from None


# ==========================================================
# List
# ==========================================================


@router.get(
    "",
    response_model=list[PromptTemplateResponse],
)
def list_prompts(
    agent_type: Optional[AgentType] = Query(
        default=None
    ),
    channel: Optional[PromptChannel] = Query(
        default=None
    ),
    language: Optional[PromptLanguage] = Query(
        default=None
    ),
    prompt_status: Optional[str] = Query(
        default=None,
        alias="status",
    ),
    is_active: Optional[bool] = Query(
        default=None
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=100,
    ),
    offset: int = Query(
        default=0,
        ge=0,
    ),
    registry: ManagedPromptTemplateRegistry = Depends(
        get_registry
    ),
) -> list[PromptTemplateResponse]:
    normalized_status = normalize_status(
        prompt_status
    )

    filters = {
        "agent_type": (
            agent_type.value
            if agent_type is not None
            else None
        ),
        "channel": (
            channel.value
            if channel is not None
            else None
        ),
        "language": (
            language.value
            if language is not None
            else None
        ),
        "status": normalized_status,
        "is_active": is_active,
        "limit": limit,
        "offset": offset,
    }

    filtered_values = {
        key: value
        for key, value in filters.items()
        if value is not None
    }

    try:
        return registry.list(
            **filtered_values
        )

    except RegistryServiceError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Prompt registry unavailable.",
        ) from None


# ==========================================================
# Lookup
# ==========================================================


@router.get(
    "/lookup",
    response_model=PromptTemplateLookupResponse,
)
def lookup_prompt(
    agent_type: AgentType = Query(...),
    channel: PromptChannel = Query(...),
    language: str = Query(...),
    prompt_status: str = Query(
        ...,
        alias="status",
        min_length=1,
    ),
    registry: ManagedPromptTemplateRegistry = Depends(
        get_registry
    ),
) -> PromptTemplateLookupResponse:
    normalized_status = normalize_status(
        prompt_status
    )

    if normalized_status is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Status is required.",
        )

    try:
        return registry.find(
            agent_type=agent_type.value,
            channel=channel.value,
            language=language,
            status=normalized_status,
        )

    except TemplateValidationServiceError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or unsupported locale.",
        ) from None

    except RegistryServiceError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Prompt registry unavailable.",
        ) from None


# ==========================================================
# Completeness
# ==========================================================

from app.services.ai.FieldOpsAI.schemas.prompt_locale import TranslationCompletenessResult
from app.services.ai.FieldOpsAI.services.prompt_locale_service import validate_translation_completeness

@router.get(
    "/translations/completeness",
    response_model=TranslationCompletenessResult,
)
def get_translations_completeness(
    agent_type: Optional[AgentType] = Query(default=None),
    channel: Optional[PromptChannel] = Query(default=None),
    prompt_status: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    principal: PromptAdminPrincipal = Depends(require_prompt_admin),
) -> TranslationCompletenessResult:
    normalized_status = normalize_status(prompt_status)
    
    target_tenant = principal.tenant_id
    if target_tenant == "**platform**":
        if principal.role != "super_admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Super admin authorization required for platform completeness.",
            )

    try:
        return validate_translation_completeness(
            db=db,
            tenant_id=target_tenant,
            limit=limit,
            offset=offset,
            agent_type=agent_type.value if agent_type else None,
            channel=channel.value if channel else None,
            status=normalized_status,
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to validate translation completeness.",
        ) from None

# ==========================================================
# Get by ID
# ==========================================================


@router.get(
    "/{template_id}",
    response_model=PromptTemplateResponse,
)
def get_prompt(
    template_id: int = Path(
        ...,
        ge=1,
    ),
    registry: ManagedPromptTemplateRegistry = Depends(
        get_registry
    ),
) -> PromptTemplateResponse:
    try:
        return registry.get(
            template_id
        )

    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found.",
        ) from None

    except RegistryServiceError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Prompt registry unavailable.",
        ) from None


# ==========================================================
# Update
# ==========================================================


@router.patch(
    "/{template_id}",
    response_model=PromptTemplateResponse,
)
def update_prompt(
    payload: PromptTemplateUpdate,
    template_id: int = Path(
        ...,
        ge=1,
    ),
    registry: ManagedPromptTemplateRegistry = Depends(
        get_registry
    ),
) -> PromptTemplateResponse:
    try:
        return registry.update(
            template_id=template_id,
            payload=payload,
        )

    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found.",
        ) from None

    except ConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The requested update conflicts "
                "with another template."
            ),
        ) from None

    except TemplateValidationServiceError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Template validation failed.",
        ) from None

    except RegistryServiceError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Prompt registry unavailable.",
        ) from None


# ==========================================================
# Soft delete
# ==========================================================


@router.delete(
    "/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_prompt(
    template_id: int = Path(
        ...,
        ge=1,
    ),
    registry: ManagedPromptTemplateRegistry = Depends(
        get_registry
    ),
) -> Response:
    try:
        registry.delete(
            template_id
        )

        return Response(
            status_code=status.HTTP_204_NO_CONTENT
        )

    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found.",
        ) from None

    except RegistryServiceError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Prompt registry unavailable.",
        ) from None


# ==========================================================
# Version History and Rollback
# ==========================================================

from app.schemas import (
    TemplateVersionResponse,
    TemplateVersionHistoryResponse,
    TemplateRollbackRequest,
    TemplateRestoreResponse,
    TemplateCompareResponse,
)
from app.services import template_version_service

@router.get(
    "/{template_id}/versions",
    response_model=TemplateVersionHistoryResponse,
    summary="List all template versions",
)
def list_template_versions(
    template_id: int = Path(..., ge=1),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    principal: PromptAdminPrincipal = Depends(require_prompt_admin)
):
    try:
        versions = template_version_service.get_versions(
            db=db,
            template_id=template_id,
            tenant_id=principal.tenant_id,
            limit=limit,
            offset=offset
        )
        current = template_version_service.get_current_version(
            db=db,
            template_id=template_id,
            tenant_id=principal.tenant_id
        )
        return {
            "template_id": template_id,
            "current_version": current,
            "versions": versions,
        }
    except (TemplateNotFoundError, VersionNotFoundError):
        raise HTTPException(status_code=404, detail="Template or version not found or inaccessible")
    except Exception as e:
        raise HTTPException(status_code=503, detail="Safe persistence failure")

@router.get(
    "/{template_id}/versions/compare",
    response_model=TemplateCompareResponse,
    summary="Compare two template versions",
)
def compare_template_versions(
    old_version: int = Query(..., ge=1),
    new_version: int = Query(..., ge=1),
    template_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    principal: PromptAdminPrincipal = Depends(require_prompt_admin)
):
    try:
        return template_version_service.compare_versions(
            db=db,
            template_id=template_id,
            old_version=old_version,
            new_version=new_version,
            tenant_id=principal.tenant_id
        )
    except (TemplateNotFoundError, VersionNotFoundError):
        raise HTTPException(status_code=404, detail="Template or version not found or inaccessible")
    except Exception as e:
        raise HTTPException(status_code=503, detail="Safe persistence failure")

@router.get(
    "/{template_id}/versions/{version_number}",
    response_model=TemplateVersionResponse,
    summary="Get one template version",
)
def get_template_version(
    version_number: int = Path(..., ge=1),
    template_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    principal: PromptAdminPrincipal = Depends(require_prompt_admin)
):
    try:
        return template_version_service.get_version(
            db=db,
            template_id=template_id,
            version_number=version_number,
            tenant_id=principal.tenant_id
        )
    except (TemplateNotFoundError, VersionNotFoundError):
        raise HTTPException(status_code=404, detail="Template or version not found or inaccessible")
    except Exception as e:
        raise HTTPException(status_code=503, detail="Safe persistence failure")

@router.post(
    "/{template_id}/versions/{version_number}/rollback",
    response_model=TemplateRestoreResponse,
    summary="Restore an older template version",
)
def restore_template_version(
    payload: TemplateRollbackRequest,
    version_number: int = Path(..., ge=1),
    template_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    principal: PromptAdminPrincipal = Depends(require_prompt_admin),
    redis_client = Depends(get_redis_client),
):
    try:
        res = template_version_service.restore_version(
            db=db,
            template_id=template_id,
            version_number=version_number,
            actor_id=principal.actor_id,
            tenant_id=principal.tenant_id,
            change_summary=payload.change_summary
        )
        db.commit()
        
        # Invalidate cache
        registry = ManagedPromptTemplateRegistry(
            db=db,
            tenant_id=principal.tenant_id,
            actor_id=principal.actor_id,
            redis_client=redis_client,
        )
        registry._invalidate_cache()
        
        return res
    except (TemplateNotFoundError, VersionNotFoundError):
        db.rollback()
        raise HTTPException(status_code=404, detail="Template or version not found or inaccessible")
    except VersionConflictError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Version conflict or invalid state")
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=503, detail="Safe persistence failure")

@router.delete(
    "/{template_id}/versions/{version_number}",
    summary="Delete a template version",
)
def delete_template_version(
    version_number: int = Path(..., ge=1),
    template_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    principal: PromptAdminPrincipal = Depends(require_prompt_admin),
    redis_client = Depends(get_redis_client),
):
    try:
        res = template_version_service.delete_version(
            db=db,
            template_id=template_id,
            version_number=version_number,
            actor_id=principal.actor_id,
            tenant_id=principal.tenant_id
        )
        db.commit()
        
        # Invalidate cache
        registry = ManagedPromptTemplateRegistry(
            db=db,
            tenant_id=principal.tenant_id,
            actor_id=principal.actor_id,
            redis_client=redis_client,
        )
        registry._invalidate_cache()
        
        return res
    except (TemplateNotFoundError, VersionNotFoundError):
        db.rollback()
        raise HTTPException(status_code=404, detail="Template or version not found or inaccessible")
    except VersionConflictError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Version conflict or invalid state")
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=503, detail="Safe persistence failure")