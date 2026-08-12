from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Any
import json
import uuid
import logging
import asyncio

from html import escape
from urllib.parse import quote

from .ai.integrations.communication_integration import (
    CommunicationIntegration,
    CommunicationIntegrationError,
)

from sqlalchemy.orm import Session
from ..database import SessionLocal
from ..models import AuditEvent, Technician, InAppNotification, Job
from ..redis_client import get_redis_client
from ..context import correlation_id_ctx
from .preferences import get_technician_preferences
from .ai.FieldOpsAI.services.communication_configuration_service import CommunicationConfigurationService
from .ai.FieldOpsAI.repositories.communication_configuration_repository import CommunicationConfigurationRepository
from .ai.FieldOpsAI.schemas.communication_configuration import (
    CommunicationMessageCategory,
    CommunicationChannelDisabledError,
)
from .ai.FieldOpsAI.services.customer_preference_service import CustomerPreferenceService
from .ai.FieldOpsAI.repositories.customer_profile_repository import CustomerProfileRepository
from .ai.FieldOpsAI.services.communication_delivery_policy_service import CommunicationDeliveryPolicyService

logger = logging.getLogger(__name__)

@dataclass
class JobStatusEvent:
    job_id: str
    tenant_id: str
    from_status: str
    to_status: str
    actor_id: str
    actor_role: str
    reason: Optional[str]
    timestamp: datetime
    job_title: str
    job_location: str
    technician_id: Optional[str]
    technician_name: Optional[str]
    customer_id: Optional[str]
    customer_name: Optional[str]
    customer_phone: Optional[str]
    customer_email: Optional[str]
    eta: Optional[str]
    notification_channels: list[str]
    event_type: str = "job_status_changed"


class SendGridService:
    """
    Send customer email through SendGrid.

    Recipient addresses and message bodies are deliberately not
    written to application logs.
    """

    def __init__(
        self,
        api_key: str | None = None,
    ) -> None:
        import os

        self.api_key = (
            api_key
            or os.getenv(
                "SENDGRID_API_KEY",
                "SG.mock_key",
            )
        )

    async def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
    ) -> bool:
        """
        Send one email or simulate delivery in local mode.
        """

        import os

        if (
            not self.api_key
            or "mock" in self.api_key
            or not os.getenv(
                "SENDGRID_API_KEY"
            )
        ):
            logger.info(
                "SendGrid email delivery simulated."
            )

            return True

        try:
            from sendgrid import (
                SendGridAPIClient,
            )
            from sendgrid.helpers.mail import (
                Mail,
            )

            message = Mail(
                from_email=os.getenv(
                    "SENDGRID_FROM_EMAIL",
                    "no-reply@fieldops.io",
                ),
                to_emails=to_email,
                subject=subject,
                html_content=html_content,
            )

            sendgrid = SendGridAPIClient(
                self.api_key
            )

            loop = asyncio.get_running_loop()

            response = await loop.run_in_executor(
                None,
                sendgrid.send,
                message,
            )

            logger.info(
                "SendGrid accepted email delivery. "
                "status_code=%s",
                response.status_code,
            )

            return response.status_code in {
                200,
                201,
                202,
            }

        except Exception:
            # Do not log the recipient address, email body,
            # API response, or exception text.
            logger.error(
                "SendGrid email delivery failed."
            )

            return False

class EventPublisher:
    def __init__(self, redis_client=None):
        self.redis = redis_client or get_redis_client()
        self.channel = "events:job_status_changed"
    
    async def publish(self, event: JobStatusEvent) -> None:
        payload_dict = {
            "event_type": event.event_type,
            "job_id": event.job_id,
            "tenant_id": event.tenant_id,
            "from_status": event.from_status,
            "to_status": event.to_status,
            "actor_id": event.actor_id,
            "actor_role": event.actor_role,
            "reason": event.reason,
            "timestamp": event.timestamp.isoformat() if hasattr(event.timestamp, "isoformat") else str(event.timestamp),
            "job_title": event.job_title,
            "job_location": event.job_location,
            "technician_id": event.technician_id,
            "technician_name": event.technician_name,
            "customer_id": event.customer_id,
            "customer_name": event.customer_name,
            "eta": event.eta,
            "notification_channels": event.notification_channels,
        }
        
        # Publish to Redis pub/sub
        if self.redis:
            try:
                self.redis.publish(self.channel, json.dumps(payload_dict))
                logger.info(f"Published status event to Redis channel {self.channel} for job {event.job_id}")
            except Exception:
                logger.error("Failed to publish status event to Redis.")
        
        # Write to audit_events table
        await self._write_audit(event)
    
    async def _write_audit(self, event: JobStatusEvent) -> None:
        db = SessionLocal()
        try:
            audit_record = AuditEvent(
                event_type="job_status_transition",
                tech_id=event.technician_id or "system",
                tenant_id=event.tenant_id,
                old_status=event.from_status,
                new_status=event.to_status,
                reason=event.reason,
                job_id=event.job_id,
                actor_id=event.actor_id,
                details={
                    "from_status": event.from_status,
                    "to_status": event.to_status,
                    "reason": event.reason,
                    "job_title": event.job_title,
                    "technician_id": event.technician_id,
                    "notification_channels": event.notification_channels,
                },
                timestamp=event.timestamp,
                correlation_id=correlation_id_ctx.get() or None,
            )
            db.add(audit_record)
            db.commit()
            logger.info(f"Written AuditEvent for transition to {event.to_status} of job {event.job_id}")
        except Exception:
            logger.error("Failed to write AuditEvent for transition.")
            db.rollback()
        finally:
            db.close()


class NotificationRouter:
    """
    Route job-status notifications to the configured delivery
    channels.

    Customer SMS and email content must come from the
    production-safe CommunicationService workflow.

    Existing technician push/SMS and dispatcher in-app
    delivery remain connected to their current services until
    their delivery adapters are upgraded.
    """

    STATUS_NOTIFICATIONS = {
        "ASSIGNED": {
            "technician": {
                "channels": [
                    "push",
                    "sms",
                ],
                "template": "technician_job_assigned",
                "priority": "high",
            },
            "dispatcher": {
                "channels": [
                    "in_app",
                ],
                "template": (
                    "dispatcher_job_assigned"
                ),
                "priority": "normal",
                "batch": True,
            },
        },

        "EN_ROUTE": {
            "technician": {
                "channels": [
                    "push",
                ],
                "template": "technician_journey_started",
                "priority": "normal",
            },
            "customer": {
                "channels": [
                    "push",
                    "sms",
                ],
                "template": (
                    "technician_en_route"
                ),
                "priority": "high",
                "include_eta": True,
            },
            "dispatcher": {
                "channels": [
                    "in_app",
                ],
                "template": (
                    "dispatcher_en_route"
                ),
                "priority": "normal",
                "batch": True,
            },
        },

        "ON_SITE": {
            "technician": {
                "channels": [
                    "push",
                ],
                "template": "technician_arrived_on_site",
                "priority": "normal",
            },
            "customer": {
                "channels": [
                    "push",
                    "sms",
                ],
                "template": (
                    "technician_arrived"
                ),
                "priority": "high",
            },
            "dispatcher": {
                "channels": [
                    "in_app",
                ],
                "template": (
                    "dispatcher_on_site"
                ),
                "priority": "normal",
                "batch": True,
            },
        },

        "COMPLETED": {
            "technician": {
                "channels": [
                    "push",
                ],
                "template": "technician_job_completed",
                "priority": "normal",
            },
            "customer": {
                "channels": [
                    "push",
                    "email",
                ],
                "template": "job_done_survey",
                "priority": "normal",
                "include_survey_link": True,
            },
            "dispatcher": {
                "channels": [
                    "in_app",
                ],
                "template": (
                    "dispatcher_completed"
                ),
                "priority": "normal",
                "batch": True,
            },
        },

        "CANCELLED": {
            "technician": {
                "channels": [
                    "push",
                    "sms",
                ],
                "template": "technician_job_cancelled",
                "priority": "high",
            },
            "customer": {
                "channels": [
                    "push",
                    "sms",
                    "email",
                ],
                "template": (
                    "job_cancelled_customer"
                ),
                "priority": "high",
            },
            "dispatcher": {
                "channels": [
                    "in_app",
                ],
                "template": (
                    "dispatcher_cancelled"
                ),
                "priority": "high",
                "batch": False,
            },
        },
    }

    # Some old routing names do not have a corresponding
    # approved built-in fallback template. Map them to the
    # canonical communication event names.
    NOTIFICATION_TYPE_ALIASES = {
        "job_assigned": "job_assigned",
        "dispatcher_job_assigned": (
            "job_assigned"
        ),

        "journey_started": (
            "technician_en_route"
        ),
        "technician_en_route": (
            "technician_en_route"
        ),
        "dispatcher_en_route": (
            "technician_en_route"
        ),

        "arrived_on_site": (
            "technician_arrived"
        ),
        "technician_arrived": (
            "technician_arrived"
        ),
        "dispatcher_on_site": (
            "technician_arrived"
        ),

        "job_completed": "job_completed",
        "job_done_survey": "job_completed",
        "dispatcher_completed": (
            "job_completed"
        ),

        "job_cancelled": "job_cancelled",
        "job_cancelled_customer": (
            "job_cancelled"
        ),
        "dispatcher_cancelled": (
            "job_cancelled"
        ),
    }

    def __init__(
        self,
        fcm_service=None,
        sms_service=None,
        email_service=None,
        ws_manager=None,
        redis_client=None,
        communication_integration=None,
    ) -> None:
        """
        Initialize notification delivery dependencies.

        communication_integration may be replaced by a fake in
        tests, preventing real AI-provider calls.
        """

        if fcm_service is None:
            from .fcm import (
                send_job_assignment_notification,
            )

            fcm_service = (
                send_job_assignment_notification
            )

        if sms_service is None:
            from .twilio_sms import (
                send_job_assignment_sms,
            )

            sms_service = (
                send_job_assignment_sms
            )

        if ws_manager is None:
            from .socket_manager import (
                ws_manager as default_ws_manager,
            )

            ws_manager = default_ws_manager

        self.fcm = fcm_service
        self.sms = sms_service

        self.email = (
            email_service
            if email_service is not None
            else SendGridService()
        )

        self.ws = ws_manager

        self.redis = (
            redis_client
            if redis_client is not None
            else get_redis_client()
        )

        self.communication = (
            communication_integration
            if communication_integration
            is not None
            else CommunicationIntegration(
                redis_client=self.redis
            )
        )
    def _evaluate_customer_delivery_policy(
        self,
        *,
        event: JobStatusEvent,
        channel: str,
        category: CommunicationMessageCategory,
    ):
        """
        Evaluate final customer delivery eligibility.

        This helper is shared by customer SMS and EMAIL.
        """

        with SessionLocal() as db:
            configuration_repository = (
                CommunicationConfigurationRepository(
                    db
                )
            )

            configuration_service = (
                CommunicationConfigurationService(
                    configuration_repository,
                    db,
                    redis_client=self.redis,
                )
            )

            preference_repository = (
                CustomerProfileRepository(
                    db
                )
            )

            preference_service = (
                CustomerPreferenceService(
                    preference_repository
                )
            )

            policy_service = (
                CommunicationDeliveryPolicyService(
                    configuration_service,
                    preference_service,
                )
            )

            return policy_service.evaluate(
                channel=channel,
                category=category,
                recipient_type="CUSTOMER",
                tenant_id=event.tenant_id,
                customer_id=event.customer_id,
            )

    # ======================================================
    # Main Routing
    # ======================================================

    async def route(
        self,
        event: JobStatusEvent,
    ) -> None:
        """
        Route one job-status event.
        """
        from app.services.ai.FieldOpsAI.schemas.prompt_template import (
            normalize_template_status,
            UnsupportedTemplateStatusError,
        )

        try:
            canon_enum = normalize_template_status(event.to_status)
            canon_status_name = canon_enum.name
            if canon_status_name == "ENROUTE":
                canon_status_name = "EN_ROUTE"
            elif canon_status_name == "ONSITE":
                canon_status_name = "ON_SITE"
        except UnsupportedTemplateStatusError:
            canon_status_name = str(event.to_status).upper()

        routing = (
            self.STATUS_NOTIFICATIONS.get(event.to_status)
            or self.STATUS_NOTIFICATIONS.get(canon_status_name)
            or {}
        )

        for recipient_type, config in routing.items():
            if not await self._check_preferences(
                event,
                recipient_type,
            ):
                continue

            payload = self._build_payload(
                event,
                recipient_type,
                config,
            )

            notification_type = (
                self._resolve_notification_type(
                    config.get(
                        "template",
                        "",
                    )
                )
            )

            for channel in config["channels"]:
                self._record_attempted_channel(
                    event,
                    channel,
                )

                if channel == "push":
                    await self._send_push(
                        event,
                        recipient_type,
                        payload,
                        config["priority"],
                        notification_type,
                    )

                elif channel == "sms":
                    try:
                        await self._send_sms(
                            event,
                            recipient_type,
                            payload,
                            notification_type,
                            category=(
                                CommunicationMessageCategory
                                .STANDARD
                            ),
                        )
                    except CommunicationChannelDisabledError:
                        # Policy block is deterministic and
                        # must not be retried as provider failure.
                        pass

                elif channel == "email":
                    try:
                        await self._send_email(
                            event,
                            recipient_type,
                            payload,
                            config,
                            notification_type,
                            category=CommunicationMessageCategory.STANDARD,
                        )
                    except CommunicationChannelDisabledError:
                        # Policy block — deterministic, non-retryable.
                        # Already logged inside _send_email.
                        pass

                elif channel == "in_app":
                    await self._send_in_app(
                        event,
                        recipient_type,
                        payload,
                        config.get(
                            "batch",
                            False,
                        ),
                        notification_type,
                    )

    # ======================================================
    # Legacy Routing Name Normalization
    # ======================================================

    @classmethod
    def _resolve_notification_type(
        cls,
        template_name: str,
    ) -> str:
        """
        Convert a legacy template name into a canonical
        CommunicationContext notification type.
        """
        from app.services.ai.FieldOpsAI.schemas.prompt_template import (
            normalize_template_status,
            UnsupportedTemplateStatusError,
        )

        normalized = str(
            template_name
        ).strip().lower()

        return cls.NOTIFICATION_TYPE_ALIASES.get(
            normalized,
            normalized,
        )

    # ======================================================
    # Safe AI Communication Generation
    # ======================================================

    async def _generate_safe_communication(
        self,
        *,
        event: JobStatusEvent,
        recipient_type: str,
        channel: str,
        notification_type: str,
    ):
        """
        Generate one final guardrail-approved communication.

        A generation failure prevents delivery. This method does
        not fall back to a hardcoded recipient-facing message,
        because CommunicationService already owns the approved
        fallback process.
        """

        try:
            result = await self.communication.generate(
                event=event,
                recipient_type=recipient_type,
                channel=channel,
                notification_type=(
                    notification_type
                ),
                locale="en",
            )

        except CommunicationIntegrationError:
            logger.error(
                "Safe communication generation failed. "
                "Notification delivery was skipped. "
                "job_id=%s channel=%s recipient_type=%s",
                event.job_id,
                channel,
                recipient_type,
            )

            return None

        except Exception:
            # Test doubles or unexpected adapter failures are
            # also handled without leaking exception content.
            logger.error(
                "Unexpected safe communication failure. "
                "Notification delivery was skipped. "
                "job_id=%s channel=%s recipient_type=%s",
                event.job_id,
                channel,
                recipient_type,
            )

            return None

        expected_channel = (
            channel
            .strip()
            .upper()
            .replace(
                "-",
                "_",
            )
        )

        if (
            result.decision.channel
            != expected_channel
        ):
            logger.error(
                "Safe communication returned the wrong "
                "channel. Notification delivery was skipped. "
                "job_id=%s",
                event.job_id,
            )

            return None

        return result

    # ======================================================
    # Payload
    # ======================================================

    def _build_payload(
        self,
        event: JobStatusEvent,
        recipient_type: str,
        config: dict,
    ) -> dict:
        """
        Build the existing structured event payload.

        This payload continues to support dispatcher digests and
        existing non-AI delivery adapters.
        """

        base = {
            "job_id": event.job_id,
            "job_title": event.job_title,
            "job_location": event.job_location,
            "status": event.to_status,
            "timestamp": (
                event.timestamp.isoformat()
                if hasattr(
                    event.timestamp,
                    "isoformat",
                )
                else str(
                    event.timestamp
                )
            ),
            "deep_link": (
                "https://fieldops.io/jobs/"
                f"{event.job_id}"
            ),
        }

        if recipient_type == "technician":
            base["technician_name"] = (
                event.technician_name
            )

        elif recipient_type == "customer":
            base["customer_name"] = (
                event.customer_name
            )

            if config.get(
                "include_eta"
            ):
                base["eta"] = (
                    event.eta
                    or "calculating..."
                )

            if config.get(
                "include_survey_link"
            ):
                base["survey_link"] = (
                    "https://fieldops.io/survey/"
                    f"{event.job_id}"
                )

        elif recipient_type == "dispatcher":
            base["actor_name"] = (
                event.actor_id
            )

        return base

    # ======================================================
    # Preferences
    # ======================================================

    async def _check_preferences(
        self,
        event: JobStatusEvent,
        recipient_type: str,
    ) -> bool:
        """
        Apply existing technician notification preferences.
        """

        if (
            recipient_type
            == "technician"
            and event.technician_id
        ):
            db = SessionLocal()

            try:
                tech = (
                    db.query(
                        Technician
                    )
                    .filter(
                        Technician.tech_id
                        == event.technician_id
                    )
                    .first()
                )

                if tech:
                    routing = (
                        self.STATUS_NOTIFICATIONS
                        .get(
                            event.to_status,
                            {},
                        )
                        .get(
                            "technician",
                            {},
                        )
                    )

                    channels = routing.get(
                        "channels",
                        [],
                    )

                    if (
                        "sms" in channels
                        and tech.sms_opt_out == 1
                    ):
                        logger.info(
                            "Technician SMS delivery is "
                            "disabled by opt-out preference. "
                            "tech_id=%s",
                            tech.tech_id,
                        )

                    preferences = (
                        get_technician_preferences(
                            db,
                            tech.tech_id,
                        )
                    )

                    if (
                        not preferences.get(
                            "sms_enabled",
                            True,
                        )
                        and not preferences.get(
                            "push_enabled",
                            True,
                        )
                        and not preferences.get(
                            "inapp_enabled",
                            True,
                        )
                    ):
                        return False

            finally:
                db.close()

        return True

    # ======================================================
    # Push Delivery — Existing Adapter
    # ======================================================

    async def _send_push(
        self,
        event: JobStatusEvent,
        recipient_type: str,
        payload: dict,
        priority: str,
        notification_type: str,
    ) -> bool:
        """
        Send guardrail-approved push communication.

        Technician push is supported because technicians have an
        FCM token in the current backend.

        Customer push cannot yet be delivered because the backend
        does not currently store a customer FCM/device token.
        """

        _ = payload

        routing_channels = (
            self.STATUS_NOTIFICATIONS
            .get(
                event.to_status,
                {},
            )
            .get(
                recipient_type,
                {},
            )
            .get(
                "channels",
                [],
            )
        )

        # ------------------------------------------------------
        # Customer push is not currently configured
        # ------------------------------------------------------

        if recipient_type != "technician":
            logger.info(
                "Push delivery is unavailable for this recipient "
                "type. job_id=%s recipient_type=%s",
                event.job_id,
                recipient_type,
            )

            # Use the already protected SMS path when the routing
            # configuration does not contain SMS.
            if (
                recipient_type == "customer"
                and "sms" not in routing_channels
            ):
                self._record_attempted_channel(
                    event,
                    "sms",
                )

                return await self._send_sms(
                    event,
                    recipient_type,
                    payload,
                    notification_type,
                )

            return False

        # ------------------------------------------------------
        # Technician push
        # ------------------------------------------------------

        if not event.technician_id:
            logger.warning(
                "Technician push skipped because the technician "
                "ID is missing. job_id=%s",
                event.job_id,
            )

            if "sms" not in routing_channels:
                self._record_attempted_channel(
                    event,
                    "sms",
                )

                return await self._send_sms(
                    event,
                    "technician",
                    payload,
                    notification_type,
                )

            return False

        db = SessionLocal()

        try:
            technician = (
                db.query(
                    Technician
                )
                .filter(
                    Technician.tech_id
                    == event.technician_id
                )
                .first()
            )

            if (
                technician is None
                or not technician.fcm_token
            ):
                logger.warning(
                    "Technician push skipped because no FCM "
                    "token is available. job_id=%s",
                    event.job_id,
                )

                if "sms" not in routing_channels:
                    self._record_attempted_channel(
                        event,
                        "sms",
                    )

                    return await self._send_sms(
                        event,
                        "technician",
                        payload,
                        notification_type,
                    )

                return False

            communication = (
                await self._generate_safe_communication(
                    event=event,
                    recipient_type="technician",
                    channel="push",
                    notification_type=(
                        notification_type
                    ),
                )
            )

            if communication is None:
                if "sms" not in routing_channels:
                    self._record_attempted_channel(
                        event,
                        "sms",
                    )

                    return await self._send_sms(
                        event,
                        "technician",
                        payload,
                        notification_type,
                    )

                return False

            title = communication.decision.output.title

            if not title:
                logger.error(
                    "Safe push communication did not contain "
                    "a title. Delivery was skipped. job_id=%s",
                    event.job_id,
                )

                return False

            if len(title) > 50:
                logger.error(
                    "Final push title exceeds the transport "
                    "limit. Delivery was skipped. job_id=%s",
                    event.job_id,
                )

                return False

            delivery_result = await self.fcm(
                db,
                event.job_id,
                event.job_title,
                event.job_location,
                [
                    event.technician_id,
                ],
                correlation_id_ctx.get(),
                notification_title=title,
                notification_body=(
                    communication.decision.output.body
                ),
                notification_type=(
                    notification_type
                ),
                priority=priority,
            )

            if isinstance(
                delivery_result,
                dict,
            ):
                return (
                    int(
                        delivery_result.get(
                            "sent",
                            0,
                        )
                    )
                    > 0
                )

            return bool(
                delivery_result
            )

        except Exception:
            logger.error(
                "Technician push delivery failed. "
                "job_id=%s",
                event.job_id,
            )

            return False

        finally:
            db.close()
    # ======================================================
    # SMS Delivery
    # ======================================================

    async def _send_sms(
        self,
        event: JobStatusEvent,
        recipient_type: str,
        payload: dict,
        notification_type: str,
        category: CommunicationMessageCategory = (
            CommunicationMessageCategory.STANDARD
        ),
    ) -> bool:
        """
        Send guardrail-approved SMS communication.

        Both customer and technician recipient-facing content now
        comes from CommunicationService.
        """

        _ = payload

        if recipient_type not in {
            "customer",
            "technician",
        }:
            return False

        # ------------------------------------------------------
        # Generate safe content first
        # ------------------------------------------------------

        communication = (
            await self._generate_safe_communication(
                event=event,
                recipient_type=recipient_type,
                channel="sms",
                notification_type=(
                    notification_type
                ),
            )
        )

        if communication is None:
            return False

        message_body = (
            communication.decision.output.text
        )

        # Final length check after real names and other values have
        # been restored.
        if len(message_body) > 160:
            logger.error(
                "Final SMS content exceeds the transport limit. "
                "Delivery was skipped. job_id=%s",
                event.job_id,
            )

            return False

        # ------------------------------------------------------
        # Technician delivery through the existing tracked SMS
        # service
        # ------------------------------------------------------

        if recipient_type == "technician":
            if not event.technician_id:
                logger.warning(
                    "Technician SMS skipped because the "
                    "technician ID is missing. job_id=%s",
                    event.job_id,
                )

                return False

            db = SessionLocal()

            try:
                delivery_result = await self.sms(
                    db,
                    event.job_id,
                    event.job_title,
                    event.job_location,
                    "HIGH",
                    [
                        event.technician_id,
                    ],
                    correlation_id_ctx.get(),
                    effective_message=message_body,
                    category=category,
                )

                if isinstance(
                    delivery_result,
                    dict,
                ):
                    return (
                        int(
                            delivery_result.get(
                                "sent",
                                0,
                            )
                        )
                        > 0
                    )

                return bool(
                    delivery_result
                )

            except Exception:
                logger.error(
                    "Technician SMS delivery failed. "
                    "job_id=%s",
                    event.job_id,
                )

                return False

            finally:
                db.close()

        # ------------------------------------------------------
        # Customer delivery
        # ------------------------------------------------------

        if not event.customer_phone:
            logger.warning(
                "Customer SMS skipped because the phone number "
                "is missing. job_id=%s",
                event.job_id,
            )

            return False
        decision = (
            self._evaluate_customer_delivery_policy(
                event=event,
                channel="SMS",
                category=category,
            )
        )

        if not decision.allowed:
            logger.warning(
                "Customer SMS delivery blocked by policy. "
                "reason_code=%s channel=SMS",
                decision.final_reason_code,
            )

            raise CommunicationChannelDisabledError(
                (
                    "SMS delivery blocked: "
                    f"{decision.final_reason_code}"
                ),
                decision,
            )

        from .twilio_sms import (
            TWILIO_ACCOUNT_SID,
            TWILIO_PHONE_NUMBER,
            dispatch_twilio_message,
        )

        local_mock_mode = (
            "dummy"
            in TWILIO_ACCOUNT_SID.lower()
        )

        if local_mock_mode:
            logger.info(
                "Customer SMS delivery simulated. "
                "job_id=%s",
                event.job_id,
            )

            return True

        try:
            loop = asyncio.get_running_loop()

            await loop.run_in_executor(
                None,
                lambda: (
                    dispatch_twilio_message(
                        body=message_body,
                        to_phone=event.customer_phone,
                    )
                ),
            )

            logger.info(
                "Customer SMS delivery completed. "
                "job_id=%s",
                event.job_id,
            )

            return True

        except Exception:
            logger.error(
                "Customer SMS delivery failed. "
                "job_id=%s",
                event.job_id,
            )

            return False  
        
    # ======================================================
    # Email Delivery
    # ======================================================

    async def _send_email(
        self,
        event: JobStatusEvent,
        recipient_type: str,
        payload: dict,
        config: dict,
        notification_type: str,
        category: CommunicationMessageCategory = CommunicationMessageCategory.STANDARD,
    ) -> bool:
        """
        Send guardrail-approved customer email.

        The survey link is generated locally by the backend. It
        is not AI-generated and does not contain customer PII.
        """

        _ = payload

        if recipient_type != "customer":
            return False

        if not event.customer_email:
            logger.warning(
                "Customer email delivery skipped because "
                "the email address is missing. job_id=%s",
                event.job_id,
            )

            return False

        communication = (
            await self._generate_safe_communication(
                event=event,
                recipient_type="customer",
                channel="email",
                notification_type=(
                    notification_type
                ),
            )
        )

        if communication is None:
            return False

        subject = (
            communication.decision.output.subject
        )

        # Preserve text alternative for Story 10
        text_body = communication.decision.output.text_body
        body_html = communication.decision.output.html_body or text_body

        if not isinstance(subject, str):
            logger.error(
                "Final email subject is not a string. "
                "Delivery was skipped. job_id=%s",
                event.job_id,
            )
            return False

        if not subject.strip():
            logger.error(
                "Final email subject is empty or blank. "
                "Delivery was skipped. job_id=%s",
                event.job_id,
            )
            return False

        if len(subject) > 78:
            logger.error(
                "Final email subject exceeds the transport limit. "
                "Delivery was skipped. job_id=%s",
                event.job_id,
            )

            return False

        # This deterministic URL is backend-generated. It is not
        # taken from AI output or free-form user input.
        if config.get(
            "include_survey_link"
        ):
            safe_job_id = quote(
                str(
                    event.job_id
                ),
                safe="",
            )

            survey_url = (
                "https://fieldops.io/survey/"
                f"{safe_job_id}"
            )

            body_html += (
                "<p>"
                "Please complete our service survey: "
                f'<a href="'
                f'{escape(survey_url, quote=True)}'
                f'">Take Survey</a>'
                "</p>"
            )

        # Enforce email delivery policy
        decision = (
            self._evaluate_customer_delivery_policy(
                event=event,
                channel="EMAIL",
                category=category,
            )
        )

        if not decision.allowed:
            logger.warning(
                "Customer email delivery blocked by policy. "
                "reason_code=%s channel=EMAIL",
                decision.final_reason_code,
            )
            raise CommunicationChannelDisabledError(
                f"Email delivery blocked: {decision.final_reason_code}",
                decision,
            )

        delivered = await self.email.send_email(
            event.customer_email,
            subject,
            body_html,
        )

        if delivered:
            logger.info(
                "Customer email delivery completed. "
                "job_id=%s",
                event.job_id,
            )

        return bool(
            delivered
        )

    # ======================================================
    # Existing Dispatcher In-App Delivery
    # ======================================================

    async def _send_in_app(
        self,
        event: JobStatusEvent,
        recipient_type: str,
        payload: dict,
        batch: bool,
        notification_type: str,
    ) -> bool:
        """
        Send guardrail-approved dispatcher in-app communication.

        Batch notifications store safe title/message content in
        Redis for the dispatcher digest.

        Immediate notifications send the same safe content through
        WebSocket.
        """

        if recipient_type != "dispatcher":
            return False

        communication = (
            await self._generate_safe_communication(
                event=event,
                recipient_type="dispatcher",
                channel="in_app",
                notification_type=(
                    notification_type
                ),
            )
        )

        if communication is None:
            return False

        safe_payload = {
            **payload,
            "notification_type": (
                notification_type
            ),
            "title": (
                communication.decision.output.title
                or "FieldOps Update"
            ),
            "message": (
                communication.decision.output.body
            ),
            "channel": "IN_APP",
        }

        if batch:
            if not self.redis:
                logger.error(
                    "Dispatcher digest queue is unavailable. "
                    "tenant_id=%s",
                    event.tenant_id,
                )

                return False

            try:
                self.redis.lpush(
                    (
                        "dispatcher_digest:"
                        f"{event.tenant_id}"
                    ),
                    json.dumps(
                        safe_payload
                    ),
                )

                logger.info(
                    "Safe dispatcher notification queued for "
                    "digest. tenant_id=%s",
                    event.tenant_id,
                )

                return True

            except Exception:
                logger.error(
                    "Dispatcher digest queueing failed. "
                    "tenant_id=%s",
                    event.tenant_id,
                )

                return False

        try:
            await self.ws.broadcast(
                (
                    "tenant:"
                    f"{event.tenant_id}:"
                    "dispatchers"
                ),
                {
                    "type": "notification",
                    "payload": safe_payload,
                },
            )

            logger.info(
                "Safe dispatcher notification broadcast. "
                "tenant_id=%s",
                event.tenant_id,
            )

            return True

        except Exception:
            logger.error(
                "Dispatcher notification broadcast failed. "
                "tenant_id=%s",
                event.tenant_id,
            )

            return False
    # ======================================================
    # Audit Helper
    # ======================================================

    @staticmethod
    def _record_attempted_channel(
        event: JobStatusEvent,
        channel: str,
    ) -> None:
        """
        Add an attempted channel without duplicates.
        """

        if channel not in event.notification_channels:
            event.notification_channels.append(
                channel
            )