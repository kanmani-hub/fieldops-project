"""
communication_service.py

Production-safe Communication Service for FieldOps Commander.

Responsibilities
----------------
- Sanitize PII before AI generation
- Generate recipient-facing communication
- Run tenant-aware communication guardrails
- Select approved Jinja2 fallback communication
- Validate fallback communication
- Persist immutable guardrail audit records
- Restore approved placeholder values locally
- Return the final safe CommunicationDecision

The service never:

- Sends SMS, email, push, or in-app notifications
- Updates job status
- Assigns technicians
- Sends original PII to an external provider
- Returns unsafe AI output
- Stores raw AI prompts or generated messages
"""

from __future__ import annotations

import logging
import os

from typing import Any, Protocol, runtime_checkable

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.services.ai.FieldOpsAI.config.agent_config_manager import AgentConfigManager
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask

from app.services.ai.pii_sanitizer import (
    PIISanitizer,
    PlaceholderMap,
    pii_sanitizer,
)
from app.services.ai.FieldOpsAI.agents.communication_agent import (
    CommunicationAgent,
)
from app.services.ai.FieldOpsAI.schemas.communication import (
    CommunicationContext,
    CommunicationDecision,
)
from app.services.ai.guardrails.audit_logger import (
    GuardrailAuditError,
    GuardrailAuditLogger,
)
from app.services.ai.guardrails.brand_safety_rule_provider import (
    DatabaseRedisBrandSafetyRuleProvider,
)
from app.services.ai.guardrails.brand_safety_validator import (
    BrandSafetyValidator,
)
from app.services.ai.guardrails.channel_validator import (
    ChannelValidator,
)
from app.services.ai.guardrails.contracts import (
    GuardrailCategory,
    GuardrailCheckResult,
    GuardrailPipelineResult,
    GuardrailSeverity,
    GuardrailViolation,
)
from app.services.ai.guardrails.fallback_service import (
    FallbackTemplateSource,
    GuardrailFallbackError,
    GuardrailFallbackResult,
    GuardrailFallbackService,
)
from app.services.ai.guardrails.length_validator import (
    LengthValidator,
)
from app.services.ai.guardrails.pii_output_detector import (
    PIIOutputDetector,
)
from app.services.ai.guardrails.pipeline import (
    GuardrailPipeline,
)
from app.services.ai.guardrails.placeholder_integrity_validator import (
    PlaceholderIntegrityValidator,
)
from app.services.ai.guardrails.profanity_validator import (
    ProfanityValidator,
)
from app.services.ai.guardrails.tone_validator import (
    ToneValidator,
)


logger = logging.getLogger(__name__)


# ==========================================================
# Communication Generator Contract
# ==========================================================


@runtime_checkable
class CommunicationGenerator(Protocol):
    """
    Contract implemented by CommunicationAgent and test fakes.
    """

    def generate(
        self,
        context: CommunicationContext,
    ) -> CommunicationDecision:
        """
        Generate one structured communication decision.
        """

        ...


# ==========================================================
# Service Result
# ==========================================================


class CommunicationServiceResult(BaseModel):
    """
    Final result returned by the production communication
    workflow.

    Unsafe AI output is never included in this result.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    decision: CommunicationDecision

    used_fallback: bool

    fallback_source: (
        FallbackTemplateSource
        | None
    ) = None

    fallback_template_id: int | None = None

    fallback_template_version: int | None = None

    guardrail_result: GuardrailPipelineResult

    audit_record_count: int = Field(
        default=0,
        ge=0,
    )

    @model_validator(
        mode="after"
    )
    def validate_result_state(
        self,
    ) -> CommunicationServiceResult:
        """
        Keep AI and fallback result fields consistent.
        """

        if self.used_fallback:
            if self.fallback_source is None:
                raise ValueError(
                    "Fallback results require a fallback source."
                )

        else:
            if self.fallback_source is not None:
                raise ValueError(
                    "AI results must not contain a fallback "
                    "source."
                )

            if self.fallback_template_id is not None:
                raise ValueError(
                    "AI results must not contain a fallback "
                    "template ID."
                )

            if self.fallback_template_version is not None:
                raise ValueError(
                    "AI results must not contain a fallback "
                    "template version."
                )

        return self


# ==========================================================
# Service Exceptions
# ==========================================================


class CommunicationServiceError(RuntimeError):
    """
    Base error for the production communication workflow.
    """


class SafeCommunicationUnavailableError(
    CommunicationServiceError
):
    """
    Raised when neither AI nor fallback output can continue
    safely.
    """


class CommunicationAuditPersistenceError(
    CommunicationServiceError
):
    """
    Raised when required immutable audit rows cannot be saved.
    """


# ==========================================================
# Production Communication Service
# ==========================================================


class CommunicationService:
    """
    Generate one safe recipient-facing communication.

    One service instance is scoped to:

    - One SQLAlchemy session
    - One trusted tenant
    """

    AUDIT_KEY_ENV_NAME = (
        "AI_GUARDRAIL_AUDIT_HMAC_KEY"
    )

    def __init__(
        self,
        *,
        db: Session,
        tenant_id: str,
        redis_client: Any | None = None,
        agent: CommunicationGenerator | None = None,
        sanitizer: PIISanitizer | None = None,
        pipeline: GuardrailPipeline | None = None,
        fallback_service: (
            GuardrailFallbackService
            | None
        ) = None,
        audit_logger: GuardrailAuditLogger | None = None,
        fingerprint_key: str | bytes | None = None,
    ) -> None:
        """
        Initialize the production communication workflow.

        Parameters
        ----------
        db
            Existing SQLAlchemy session.

        tenant_id
            Trusted tenant identifier supplied by the backend
            request or business-service layer.

        redis_client
            Existing FieldOps Redis client. Redis is optional;
            PostgreSQL remains the source of truth.

        agent
            CommunicationAgent or a test replacement.

        sanitizer
            Request-scoped PII sanitization implementation.

        pipeline
            Optional custom guardrail pipeline.

        fallback_service
            Optional custom fallback renderer.

        audit_logger
            Optional custom audit logger.

        fingerprint_key
            HMAC secret. When omitted, it is loaded from
            AI_GUARDRAIL_AUDIT_HMAC_KEY.
        """

        normalized_tenant_id = tenant_id.strip()

        if not normalized_tenant_id:
            raise ValueError(
                "tenant_id must not be empty."
            )

        if len(normalized_tenant_id) > 50:
            raise ValueError(
                "tenant_id exceeds the supported length."
            )

        self._db = db
        self._tenant_id = normalized_tenant_id

        if agent is not None:
            self._agent = agent
        else:
            config_manager = AgentConfigManager()
            config = config_manager.resolve(
                agent_type=AITask.COMMUNICATION,
                tenant_id=normalized_tenant_id,
            )
            self._agent = CommunicationAgent(config=config)

        self._sanitizer = (
            sanitizer
            if sanitizer is not None
            else pii_sanitizer
        )

        self._fallback_service = (
            fallback_service
            if fallback_service is not None
            else GuardrailFallbackService(
                db=db
            )
        )

        self._pipeline = (
            pipeline
            if pipeline is not None
            else self._build_production_pipeline(
                db=db,
                tenant_id=normalized_tenant_id,
                redis_client=redis_client,
            )
        )

        if audit_logger is not None:
            self._audit_logger = audit_logger

        else:
            effective_fingerprint_key = (
                fingerprint_key
                if fingerprint_key is not None
                else os.getenv(
                    self.AUDIT_KEY_ENV_NAME
                )
            )

            if not effective_fingerprint_key:
                raise ValueError(
                    "AI_GUARDRAIL_AUDIT_HMAC_KEY must be "
                    "configured."
                )

            self._audit_logger = GuardrailAuditLogger(
                db=db,
                fingerprint_key=(
                    effective_fingerprint_key
                ),
            )

    # ======================================================
    # Public Workflow
    # ======================================================

    def generate(
        self,
        *,
        context: CommunicationContext,
    ) -> CommunicationServiceResult:
        """
        Generate and return one final safe communication.

        Processing order
        ----------------
        1. Sanitize the original context
        2. Send only sanitized context to the agent
        3. Validate generated output
        4. Select fallback when required
        5. Validate fallback output
        6. Save required audit rows
        7. Restore placeholders locally
        """

        if not isinstance(
            context,
            CommunicationContext,
        ):
            raise TypeError(
                "context must be a CommunicationContext."
            )

        sanitization_result = (
            self._sanitizer.sanitize(
                context.model_dump(
                    mode="python"
                )
            )
        )

        placeholder_map = (
            sanitization_result.placeholder_map
        )

        try:
            sanitized_context = (
                CommunicationContext.model_validate(
                    sanitization_result.sanitized_data
                )
            )

            # ----------------------------------------------
            # AI generation
            # ----------------------------------------------

            try:
                ai_decision = self._agent.generate(
                    sanitized_context
                )

                if not isinstance(
                    ai_decision,
                    CommunicationDecision,
                ):
                    raise TypeError(
                        "Communication Agent returned an "
                        "invalid response type."
                    )

            except Exception:
                # Do not log the exception message. Provider or
                # parsing exceptions may contain generated text.
                logger.warning(
                    "AI communication generation failed. "
                    "Approved fallback will be attempted."
                )

                generation_failure = (
                    self._build_generation_failure_result()
                )

                return self._process_fallback(
                    sanitized_context=(
                        sanitized_context
                    ),
                    placeholder_map=placeholder_map,
                    original_guardrail_result=(
                        generation_failure
                    ),
                    original_decision=None,
                )

            # ----------------------------------------------
            # Validate AI output
            # ----------------------------------------------

            guardrail_result = (
                self._pipeline.run(
                    context=sanitized_context,
                    decision=ai_decision,
                )
            )

            if guardrail_result.passed:
                final_decision = (
                    self._restore_decision(
                        decision=ai_decision,
                        placeholder_map=placeholder_map,
                    )
                )

                return CommunicationServiceResult(
                    decision=final_decision,
                    used_fallback=False,
                    fallback_source=None,
                    fallback_template_id=None,
                    fallback_template_version=None,
                    guardrail_result=guardrail_result,
                    audit_record_count=0,
                )

            if guardrail_result.blocked:
                audit_count = (
                    self._persist_audit_results(
                        entries=(
                            (
                                "communication_agent",
                                ai_decision,
                                guardrail_result,
                                False,
                            ),
                        ),
                        context=sanitized_context,
                    )
                )

                _ = audit_count

                raise SafeCommunicationUnavailableError(
                    "Generated communication was blocked by "
                    "the guardrail pipeline."
                )

            return self._process_fallback(
                sanitized_context=sanitized_context,
                placeholder_map=placeholder_map,
                original_guardrail_result=(
                    guardrail_result
                ),
                original_decision=ai_decision,
            )

        finally:
            # restore_data normally clears this mapping.
            # This final clear also covers every failure path.
            placeholder_map.clear()

    # ======================================================
    # Fallback Processing
    # ======================================================

    def _process_fallback(
        self,
        *,
        sanitized_context: CommunicationContext,
        placeholder_map: PlaceholderMap,
        original_guardrail_result: GuardrailPipelineResult,
        original_decision: CommunicationDecision | None,
    ) -> CommunicationServiceResult:
        """
        Render, validate, audit, and restore a safe fallback.
        """

        try:
            fallback = self._fallback_service.render(
                context=sanitized_context
            )

        except GuardrailFallbackError as exc:
            audit_decision = (
                original_decision
                if original_decision is not None
                else self._build_audit_placeholder_decision(
                    sanitized_context.channel
                )
            )

            self._persist_audit_results(
                entries=(
                    (
                        "communication_agent",
                        audit_decision,
                        original_guardrail_result,
                        False,
                    ),
                ),
                context=sanitized_context,
            )

            raise SafeCommunicationUnavailableError(
                "No approved safe fallback communication "
                "could be rendered."
            ) from exc

        fallback_guardrail_result = (
            self._pipeline.run(
                context=sanitized_context,
                decision=fallback.decision,
            )
        )

        # Approved templates are validated again. This protects
        # against a misconfigured database template.
        if not fallback_guardrail_result.passed:
            original_audit_decision = (
                original_decision
                if original_decision is not None
                else fallback.decision
            )

            self._persist_audit_results(
                entries=(
                    (
                        "communication_agent",
                        original_audit_decision,
                        original_guardrail_result,
                        False,
                    ),
                    (
                        "guardrail_fallback_service",
                        fallback.decision,
                        fallback_guardrail_result,
                        False,
                    ),
                ),
                context=sanitized_context,
            )

            raise SafeCommunicationUnavailableError(
                "Fallback communication also failed the "
                "guardrail pipeline."
            )

        original_audit_decision = (
            original_decision
            if original_decision is not None
            else fallback.decision
        )

        audit_count = self._persist_audit_results(
            entries=(
                (
                    "communication_agent",
                    original_audit_decision,
                    original_guardrail_result,
                    True,
                ),
            ),
            context=sanitized_context,
        )

        restored_decision = self._restore_decision(
            decision=fallback.decision,
            placeholder_map=placeholder_map,
        )

        return CommunicationServiceResult(
            decision=restored_decision,
            used_fallback=True,
            fallback_source=fallback.source,
            fallback_template_id=(
                fallback.template_id
            ),
            fallback_template_version=(
                fallback.template_version
            ),
            guardrail_result=(
                original_guardrail_result
            ),
            audit_record_count=audit_count,
        )

    # ======================================================
    # Audit Persistence
    # ======================================================

    def _persist_audit_results(
        self,
        *,
        entries: tuple[
            tuple[
                str,
                CommunicationDecision,
                GuardrailPipelineResult,
                bool,
            ],
            ...,
        ],
        context: CommunicationContext,
    ) -> int:
        """
        Persist and commit all required audit rows.

        Each entry contains:

        - agent name
        - decision being fingerprinted
        - pipeline result
        - whether fallback succeeded
        """

        record_count = 0

        try:
            for (
                agent_name,
                decision,
                result,
                fallback_triggered,
            ) in entries:
                records = (
                    self._audit_logger
                    .record_pipeline_result(
                        tenant_id=self._tenant_id,
                        context=context,
                        decision=decision,
                        result=result,
                        fallback_triggered=(
                            fallback_triggered
                        ),
                        agent_name=agent_name,
                        prompt_payload=context,
                    )
                )

                record_count += len(
                    records
                )

            self._db.commit()

        except (
            GuardrailAuditError,
            SQLAlchemyError,
        ) as exc:
            self._db.rollback()

            raise CommunicationAuditPersistenceError(
                "Required communication guardrail audit "
                "records could not be saved."
            ) from exc

        return record_count

    # ======================================================
    # Placeholder Restoration
    # ======================================================

    def _restore_decision(
        self,
        *,
        decision: CommunicationDecision,
        placeholder_map: PlaceholderMap,
    ) -> CommunicationDecision:
        """
        Restore approved values locally after safety validation.

        CommunicationDecision is converted to a dictionary
        because PIISanitizer restores nested mappings and text.
        """

        restored_payload = (
            self._sanitizer.restore_data(
                data=decision.model_dump(
                    mode="python"
                ),
                placeholder_map=placeholder_map,
                clear_mapping=True,
            )
        )

        return CommunicationDecision.model_validate(
            restored_payload
        )

    # ======================================================
    # Pipeline Construction
    # ======================================================

    @staticmethod
    def _build_production_pipeline(
        *,
        db: Session,
        tenant_id: str,
        redis_client: Any | None,
    ) -> GuardrailPipeline:
        """
        Build the tenant-aware production pipeline.

        The order matches the tested default guardrail order.
        """

        brand_rule_provider = (
            DatabaseRedisBrandSafetyRuleProvider(
                db=db,
                tenant_id=tenant_id,
                redis_client=redis_client,
            )
        )

        return GuardrailPipeline(
            checkers=(
                ChannelValidator(),
                LengthValidator(),
                PlaceholderIntegrityValidator(),
                PIIOutputDetector(),
                ProfanityValidator(),
                BrandSafetyValidator(
                    rule_provider=(
                        brand_rule_provider
                    )
                ),
                ToneValidator(),
            ),
            fail_fast=True,
        )

    # ======================================================
    # Safe System Failure Result
    # ======================================================

    @staticmethod
    def _build_generation_failure_result(
    ) -> GuardrailPipelineResult:
        """
        Build an audit-safe result for provider, parsing, or
        response-schema failure.

        The exception text and raw provider response are not
        stored.
        """

        check = GuardrailCheckResult(
            checker_name="provider_execution",
            passed=False,
            violations=(
                GuardrailViolation(
                    code="AI_GENERATION_FAILED",
                    category=(
                        GuardrailCategory.SYSTEM
                    ),
                    severity=(
                        GuardrailSeverity.CRITICAL
                    ),
                    message=(
                        "AI generation did not produce a "
                        "validated communication decision."
                    ),
                    field="response",
                    safe_metadata={
                        "stage": "ai_generation",
                    },
                ),
            ),
            latency_ms=0.0,
        )

        return GuardrailPipelineResult.from_checks(
            checks=(
                check,
            ),
            total_latency_ms=0.0,
            reason=(
                "AI generation failed and required an "
                "approved fallback."
            ),
        )

    # ======================================================
    # Audit-Only Placeholder Decision
    # ======================================================

    @staticmethod
    def _build_audit_placeholder_decision(
        channel: str,
    ) -> CommunicationDecision:
        """
        Build safe content used only for audit fingerprinting
        when no AI decision exists and fallback rendering fails.

        The content is never sent to a recipient.
        """

        if channel == "EMAIL":
            return CommunicationDecision(
                channel="EMAIL",
                title=None,
                subject="FieldOps communication unavailable",
                message=(
                    "A safe communication could not be "
                    "generated."
                ),
                tone="PROFESSIONAL",
                confidence=1.0,
            )

        if channel == "PUSH":
            return CommunicationDecision(
                channel="PUSH",
                title="FieldOps update",
                subject=None,
                message=(
                    "A safe communication could not be "
                    "generated."
                ),
                tone="PROFESSIONAL",
                confidence=1.0,
            )

        if channel == "IN_APP":
            return CommunicationDecision(
                channel="IN_APP",
                title="FieldOps update",
                subject=None,
                message=(
                    "A safe communication could not be "
                    "generated."
                ),
                tone="PROFESSIONAL",
                confidence=1.0,
            )

        return CommunicationDecision(
            channel="SMS",
            title=None,
            subject=None,
            message=(
                "A safe communication could not be generated."
            ),
            tone="PROFESSIONAL",
            confidence=1.0,
        )