from typing import Optional

from sqlalchemy.exc import SQLAlchemyError

from ..schemas.communication import CommunicationRecipient
from ..schemas.communication_configuration import (
    CommunicationMessageCategory,
)
from ..schemas.communication_delivery_policy import (
    CommunicationDeliveryEligibilityDecision,
    CommunicationDeliveryEligibilityInput,
)
from .communication_configuration_service import (
    CommunicationConfigurationService,
)
from .customer_preference_service import (
    CustomerPreferenceError,
    CustomerPreferenceService,
)

class CommunicationDeliveryPolicyService:
    def __init__(
        self,
        configuration_service: CommunicationConfigurationService,
        preference_service: CustomerPreferenceService,
    ) -> None:
        self.configuration_service = configuration_service
        self.preference_service = preference_service

    def evaluate(
        self,
        *,
        channel: str,
        category: CommunicationMessageCategory,
        recipient_type: CommunicationRecipient,
        tenant_id: Optional[str] = None,
        customer_id: Optional[str] = None,
    ) -> CommunicationDeliveryEligibilityDecision:
        
        request = CommunicationDeliveryEligibilityInput(
            channel=channel,
            category=category,
            recipient_type=recipient_type,
            tenant_id=tenant_id,
            customer_id=customer_id,
        )

        channel = request.channel
        category = request.category
        recipient_type = request.recipient_type
        tenant_id = request.tenant_id
        customer_id = request.customer_id

        # 1. Evaluate global configuration
        global_decision = self.configuration_service.evaluate_delivery(
            channel=channel,
            category=category,
        )

        global_allowed = global_decision.allowed
        global_state = global_decision.state
        global_reason_code = global_decision.reason_code
        global_revision = global_decision.revision

        # If globally blocked, short-circuit
        if not global_allowed:
            return CommunicationDeliveryEligibilityDecision(
                allowed=False,
                channel=channel,
                category=category,
                recipient_type=recipient_type,
                global_allowed=global_allowed,
                global_state=global_state,
                global_reason_code=global_reason_code,
                global_revision=global_revision,
                preference_applied=False,
                preference_allowed=None,
                preference_reason_code=None,
                preference_source=None,
                preference_revision=None,
                final_reason_code=global_reason_code,
            )

        # 2. If recipient is NOT CUSTOMER, skip preference check
        if recipient_type != CommunicationRecipient.CUSTOMER:
            return CommunicationDeliveryEligibilityDecision(
                allowed=True,
                channel=channel,
                category=category,
                recipient_type=recipient_type,
                global_allowed=global_allowed,
                global_state=global_state,
                global_reason_code=global_reason_code,
                global_revision=global_revision,
                preference_applied=False,
                preference_allowed=None,
                preference_reason_code=None,
                preference_source=None,
                preference_revision=None,
                final_reason_code="DELIVERY_POLICY_ALLOWED",
            )

        # 3. Handle CUSTOMER preference
        if not tenant_id or not str(tenant_id).strip() or not customer_id or not str(customer_id).strip():
            # Customer identity is required for customer delivery
            return CommunicationDeliveryEligibilityDecision(
                allowed=False,
                channel=channel,
                category=category,
                recipient_type=recipient_type,
                global_allowed=global_allowed,
                global_state=global_state,
                global_reason_code=global_reason_code,
                global_revision=global_revision,
                preference_applied=True,
                preference_allowed=False,
                preference_reason_code="CUSTOMER_IDENTITY_REQUIRED",
                preference_source=None,
                preference_revision=None,
                final_reason_code="CUSTOMER_IDENTITY_REQUIRED",
            )

        try:
            preference_decision = self.preference_service.evaluate_channel(
                tenant_id=tenant_id,
                customer_id=customer_id,
                channel=channel,
            )
        except (
            CustomerPreferenceError,
            SQLAlchemyError,
        ):
            return CommunicationDeliveryEligibilityDecision(
                allowed=False,
                channel=channel,
                category=category,
                recipient_type=recipient_type,
                global_allowed=global_allowed,
                global_state=global_state,
                global_reason_code=global_reason_code,
                global_revision=global_revision,
                preference_applied=True,
                preference_allowed=False,
                preference_reason_code="CUSTOMER_PREFERENCE_UNAVAILABLE",
                preference_source=None,
                preference_revision=None,
                final_reason_code="CUSTOMER_PREFERENCE_UNAVAILABLE",
            )

        preference_allowed = preference_decision.allowed
        preference_reason_code = preference_decision.reason_code
        preference_source = preference_decision.source
        preference_revision = preference_decision.revision

        if preference_allowed:
            final_reason_code = "DELIVERY_POLICY_ALLOWED"
            allowed = True
        else:
            final_reason_code = preference_reason_code
            allowed = False

        return CommunicationDeliveryEligibilityDecision(
            allowed=allowed,
            channel=channel,
            category=category,
            recipient_type=recipient_type,
            global_allowed=global_allowed,
            global_state=global_state,
            global_reason_code=global_reason_code,
            global_revision=global_revision,
            preference_applied=True,
            preference_allowed=preference_allowed,
            preference_reason_code=preference_reason_code,
            preference_source=preference_source,
            preference_revision=preference_revision,
            final_reason_code=final_reason_code,
        )
