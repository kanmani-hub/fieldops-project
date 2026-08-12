from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from app.dependencies.prompt_admin_authorization import require_prompt_admin, PromptAdminPrincipal
from ..redis_client import get_redis_client
from ..services.ai.FieldOpsAI.schemas.communication_configuration import (
    CommunicationChannelStateUpdate,
    CommunicationConfigurationResponse,
    UnsupportedCommunicationChannelError,
    CommunicationConfigurationUnavailableError,
    CommunicationConfigurationNotFoundError,
    CommunicationConfigurationConflictError,
)
from ..services.ai.FieldOpsAI.repositories.communication_configuration_repository import CommunicationConfigurationRepository
from ..services.ai.FieldOpsAI.services.communication_configuration_service import CommunicationConfigurationService
from ..context import correlation_id_ctx

def require_platform_super_admin(principal: PromptAdminPrincipal = Depends(require_prompt_admin)) -> PromptAdminPrincipal:
    if principal.tenant_id != "**platform**" or principal.role != "super_admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Platform super-admin access required.")
    return principal

router = APIRouter( 
    prefix="/admin/communication-config/channels",
    tags=["communication-config"]
)

def get_config_service(
    db: Session = Depends(get_db),
    redis_client=Depends(get_redis_client),
) -> CommunicationConfigurationService:
    repository = CommunicationConfigurationRepository(db)
    return CommunicationConfigurationService(
        repository,
        db,
        redis_client=redis_client,
    )

@router.get("/{channel}", response_model=CommunicationConfigurationResponse)
def get_channel_configuration(
    channel: str,
    principal: PromptAdminPrincipal = Depends(require_platform_super_admin),
    service: CommunicationConfigurationService = Depends(get_config_service)
):
    try:
        return service.get_channel_configuration(channel)
    except UnsupportedCommunicationChannelError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported communication channel.",
        ) from None

    except CommunicationConfigurationUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Communication configuration unavailable.",
        ) from None

    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Communication configuration unavailable.",
        ) from None

@router.put("/{channel}", response_model=CommunicationConfigurationResponse)
def update_channel_configuration(
    channel: str,
    update: CommunicationChannelStateUpdate,
    principal: PromptAdminPrincipal = Depends(require_platform_super_admin),
    service: CommunicationConfigurationService = Depends(get_config_service)
):
    try:
        correlation_id = correlation_id_ctx.get()
        return service.update_channel_state(
            channel=channel,
            new_state=update.state,
            actor_id=principal.actor_id,
            actor_tenant_id=principal.tenant_id,
            reason=update.reason,
            correlation_id=correlation_id
        )
    except UnsupportedCommunicationChannelError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported communication channel.")
    except CommunicationConfigurationNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Communication configuration not found.")
    except CommunicationConfigurationConflictError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Communication configuration conflict.")
    except CommunicationConfigurationUnavailableError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Communication configuration unavailable.")
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Communication configuration unavailable.",
        ) from None
