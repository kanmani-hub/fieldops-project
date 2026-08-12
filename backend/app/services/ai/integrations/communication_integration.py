"""
communication_integration.py

Integration adapter between the existing FieldOps notification
workflow and the production-safe AI CommunicationService.

Responsibilities
----------------
- Convert job-status events into CommunicationContext
- Normalize channels and recipient types
- Pass the trusted tenant ID to CommunicationService
- Open and close a dedicated SQLAlchemy session
- Execute synchronous AI generation outside the async event loop
- Return only a validated CommunicationServiceResult

The integration never:

- Sends SMS, email, push, or in-app notifications
- Updates job status
- Assigns technicians
- Builds unguarded recipient-facing messages
- Logs raw generated content or PII
"""

from __future__ import annotations

import asyncio
import logging

from collections.abc import Callable
from typing import Any, Protocol

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app import database as database_module
from app.context import correlation_id_ctx
from app.redis_client import get_redis_client
from app.services.ai.FieldOpsAI.schemas.communication import (
    CommunicationContext,
)
from app.services.ai.FieldOpsAI.services.communication_service import (
    CommunicationService,
    CommunicationServiceResult,
)


logger = logging.getLogger(__name__)


# ==========================================================
# Event Contract
# ==========================================================


class JobStatusEventLike(Protocol):
    """
    Minimum job-status event fields required by this adapter.

    Using a protocol avoids importing NotificationRouter here,
    which would create a circular import later.
    """

    job_id: str
    tenant_id: str

    to_status: str

    job_title: str

    technician_name: str | None

    customer_name: str | None

    eta: str | None


# ==========================================================
# Exceptions
# ==========================================================


class CommunicationIntegrationError(RuntimeError):
    """
    Safe error raised when recipient-facing content cannot be
    generated through the production communication workflow.
    """


# ==========================================================
# Integration Adapter
# ==========================================================


class CommunicationIntegration:
    """
    Connect job-status notifications to CommunicationService.

    One call creates its own database session because the
    notification workflow may run inside an asynchronous task
    or Celery worker.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[
            [],
            Session,
        ]
        | None = None,
        redis_client: Any | None = None,
        service_factory: Callable[
            ...,
            CommunicationService,
        ] = CommunicationService,
    ) -> None:
        """
        Initialize the communication integration.

        Parameters
        ----------
        session_factory
            Creates one SQLAlchemy session for each generation
            request.

        redis_client
            Existing FieldOps Redis client.

        service_factory
            CommunicationService class or a test replacement.
        """

        self._session_factory = (
            session_factory
            if session_factory is not None
            else database_module.SessionLocal
        )

        self._redis_client = (
            redis_client
            if redis_client is not None
            else get_redis_client()
        )

        self._service_factory = service_factory

    # ======================================================
    # Public API
    # ======================================================

    async def generate(
        self,
        *,
        event: JobStatusEventLike,
        recipient_type: str,
        channel: str,
        notification_type: str,
        locale: str = "en",
    ) -> CommunicationServiceResult:
        """
        Generate one safe communication for a status event.

        CommunicationService is synchronous because it performs
        SQLAlchemy and provider operations. asyncio.to_thread()
        prevents it from blocking the existing async
        NotificationRouter event loop.
        """

        context = self.build_context(
            event=event,
            recipient_type=recipient_type,
            channel=channel,
            notification_type=notification_type,
            locale=locale,
        )

        tenant_id = self._normalize_required_text(
            event.tenant_id,
            field_name="tenant_id",
        )

        try:
            return await asyncio.to_thread(
                self._generate_sync,
                tenant_id,
                context,
            )

        except CommunicationIntegrationError:
            raise

        except Exception as exc:
            # Never include exception details. Provider and
            # template errors may contain generated content.
            logger.warning(
                "Production communication integration failed."
            )

            raise CommunicationIntegrationError(
                "Safe communication could not be generated."
            ) from exc

    # ======================================================
    # Context Construction
    # ======================================================

    def build_context(
        self,
        *,
        event: JobStatusEventLike,
        recipient_type: str,
        channel: str,
        notification_type: str,
        locale: str = "en",
    ) -> CommunicationContext:
        """
        Convert an existing job-status event into the strict
        CommunicationContext schema.
        """

        normalized_channel = (
            self._normalize_channel(
                channel
            )
        )

        normalized_recipient = (
            self._normalize_recipient(
                recipient_type
            )
        )

        from app.services.ai.FieldOpsAI.schemas.prompt_template import (
            normalize_template_status,
            UnsupportedTemplateStatusError,
            MessageTemplateStatus,
        )

        raw_event_status = self._normalize_required_text(
            event.to_status,
            field_name="job_status",
        )

        try:
            status_enum = normalize_template_status(raw_event_status, allow_default=False)
            canon_status = status_enum.value if hasattr(status_enum, "value") else str(status_enum)
        except UnsupportedTemplateStatusError:
            raise CommunicationIntegrationError("Could not create a valid communication context.") from None

        raw_notif_type = self._normalize_required_text(
            notification_type,
            field_name="notification_type",
        ).lower()

        try:
            notif_enum = normalize_template_status(raw_notif_type, allow_default=False)
            if isinstance(notif_enum, MessageTemplateStatus) and isinstance(status_enum, MessageTemplateStatus):
                if notif_enum != status_enum:
                    raise CommunicationIntegrationError("TEMPLATE_STATUS_CONFLICT")
        except UnsupportedTemplateStatusError:
            pass

        effective_notif_type = raw_notif_type

        # Map enum name to CommunicationContext JobStatus Literal
        status_map = {
            "enroute": "EN_ROUTE",
            "onsite": "ON_SITE",
        }
        normalized_status = status_map.get(canon_status, canon_status.upper())

        normalized_locale = (
            self._normalize_required_text(
                locale,
                field_name="locale",
            )
        )

        try:
            return CommunicationContext(
                job_id=self._normalize_required_text(
                    str(
                        event.job_id
                    ),
                    field_name="job_id",
                ),
                correlation_id=self._clean_optional_text(
                    correlation_id_ctx.get()
                ),
                notification_type=(
                    effective_notif_type
                ),
                recipient_type=(
                    normalized_recipient
                ),
                channel=normalized_channel,
                locale=normalized_locale,
                customer_name=(
                    self._clean_optional_text(
                        event.customer_name
                    )
                ),
                technician_name=(
                    self._clean_optional_text(
                        event.technician_name
                    )
                ),
                job_status=normalized_status,
                job_title=(
                    self._clean_optional_text(
                        event.job_title
                    )
                ),
                eta=self._clean_optional_text(
                    event.eta
                ),
                appointment_time=None,
                sentiment="NEUTRAL",
                additional_context=None,
            )

        except ValidationError as exc:
            # Pydantic errors can contain original input values.
            # Do not expose the complete ValidationError.
            raise CommunicationIntegrationError(
                "The job-status event could not be converted "
                "into a valid communication context."
            ) from exc

    # ======================================================
    # Synchronous Production Execution
    # ======================================================

    def _generate_sync(
        self,
        tenant_id: str,
        context: CommunicationContext,
    ) -> CommunicationServiceResult:
        """
        Execute CommunicationService using a dedicated database
        session.

        This method runs inside asyncio.to_thread().
        """

        db: Session | None = None

        try:
            db = self._session_factory()

            service = self._service_factory(
                db=db,
                tenant_id=tenant_id,
                redis_client=self._redis_client,
            )

            result = service.generate(
                context=context
            )

            if not isinstance(
                result,
                CommunicationServiceResult,
            ):
                raise TypeError(
                    "CommunicationService returned an invalid "
                    "result type."
                )

            return result

        except CommunicationIntegrationError:
            raise

        except Exception as exc:
            logger.warning(
                "Safe communication generation failed inside "
                "the notification integration."
            )

            raise CommunicationIntegrationError(
                "Safe communication could not be generated."
            ) from exc

        finally:
            if db is not None:
                try:
                    db.close()

                except Exception:
                    logger.warning(
                        "Communication database session could "
                        "not be closed cleanly."
                    )

    # ======================================================
    # Normalization Helpers
    # ======================================================

    @staticmethod
    def _normalize_channel(
        channel: str,
    ) -> str:
        """
        Convert existing lower-case channel names into the
        CommunicationContext channel format.

        Examples
        --------
        sms     -> SMS
        email   -> EMAIL
        push    -> PUSH
        in_app  -> IN_APP
        """

        normalized = (
            CommunicationIntegration
            ._normalize_required_text(
                channel,
                field_name="channel",
            )
            .upper()
            .replace(
                "-",
                "_",
            )
        )

        supported_channels = {
            "SMS",
            "EMAIL",
            "PUSH",
            "IN_APP",
        }

        if normalized not in supported_channels:
            raise CommunicationIntegrationError(
                "The notification channel is not supported."
            )

        return normalized

    # ------------------------------------------------------

    @staticmethod
    def _normalize_recipient(
        recipient_type: str,
    ) -> str:
        """
        Normalize the recipient type.

        Examples
        --------
        customer   -> CUSTOMER
        technician -> TECHNICIAN
        dispatcher -> DISPATCHER
        """

        normalized = (
            CommunicationIntegration
            ._normalize_required_text(
                recipient_type,
                field_name="recipient_type",
            )
            .upper()
        )

        supported_recipients = {
            "CUSTOMER",
            "TECHNICIAN",
            "DISPATCHER",
        }

        if normalized not in supported_recipients:
            raise CommunicationIntegrationError(
                "The notification recipient type is not "
                "supported."
            )

        return normalized

    # ------------------------------------------------------

    @staticmethod
    def _normalize_required_text(
        value: Any,
        *,
        field_name: str,
    ) -> str:
        """
        Normalize one required text value.
        """

        if value is None:
            raise CommunicationIntegrationError(
                f"{field_name} is required."
            )

        normalized = str(
            value
        ).strip()

        if not normalized:
            raise CommunicationIntegrationError(
                f"{field_name} must not be empty."
            )

        return normalized

    # ------------------------------------------------------

    @staticmethod
    def _clean_optional_text(
        value: Any,
    ) -> str | None:
        """
        Convert blank optional values into None.

        This prevents Pydantic min-length errors and prevents
        blank strings from entering templates.
        """

        if value is None:
            return None

        normalized = str(
            value
        ).strip()

        return (
            normalized
            if normalized
            else None
        )