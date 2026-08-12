"""
brand_safety_admin_schemas.py

Request and response contracts for tenant-specific
brand-safety administration.

These schemas are used by:

- BrandSafetyAdminService
- Future FastAPI administration routes
- Unit and integration tests

They do not access the database or Redis.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from app.services.ai.guardrails.brand_safety_validator import (
    BrandSafetyMatchType,
    BrandSafetyRule,
    BrandSafetyRuleCategory,
)
from app.services.ai.guardrails.contracts import (
    GuardrailSeverity,
)


class BrandSafetyRuleCreate(BaseModel):
    """
    Data required to create one tenant-specific rule.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    rule_id: str = Field(
        ...,
        min_length=3,
        max_length=100,
        pattern=r"^[A-Z][A-Z0-9_]*$",
        description=(
            "Stable uppercase rule identifier, such as "
            "COMPETITOR_ACME."
        ),
    )

    category: BrandSafetyRuleCategory = Field(
        ...,
        description="Business category of the rule.",
    )

    match_type: BrandSafetyMatchType = Field(
        ...,
        description="Complete-word or complete-phrase matching.",
    )

    pattern: str = Field(
        ...,
        min_length=2,
        max_length=200,
        description="Word or phrase that should be detected.",
    )

    severity: GuardrailSeverity = Field(
        default=GuardrailSeverity.ERROR,
        description="Severity used when this rule matches.",
    )

    active: bool = Field(
        default=True,
        description="Whether the rule should run.",
    )

    case_sensitive: bool = Field(
        default=False,
        description="Whether letter casing must match exactly.",
    )

    @model_validator(
        mode="after"
    )
    def validate_complete_rule(
        self,
    ) -> BrandSafetyRuleCreate:
        """
        Reuse the production BrandSafetyRule validation.

        This ensures the administration API cannot save a rule
        that BrandSafetyValidator would later reject.
        """

        BrandSafetyRule(
            rule_id=self.rule_id,
            category=self.category,
            match_type=self.match_type,
            pattern=self.pattern,
            severity=self.severity,
            active=self.active,
            case_sensitive=self.case_sensitive,
        )

        return self


class BrandSafetyRuleUpdate(BaseModel):
    """
    Fields that may be changed on an existing rule.

    rule_id is intentionally not included because it is a
    stable machine-readable identifier.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    category: BrandSafetyRuleCategory | None = Field(
        default=None,
    )

    match_type: BrandSafetyMatchType | None = Field(
        default=None,
    )

    pattern: str | None = Field(
        default=None,
        min_length=2,
        max_length=200,
    )

    severity: GuardrailSeverity | None = Field(
        default=None,
    )

    active: bool | None = Field(
        default=None,
    )

    case_sensitive: bool | None = Field(
        default=None,
    )

    @model_validator(
        mode="after"
    )
    def validate_update_payload(
        self,
    ) -> BrandSafetyRuleUpdate:
        """
        Require at least one non-null update field.
        """

        if not self.model_fields_set:
            raise ValueError(
                "At least one rule field must be supplied."
            )

        for field_name in self.model_fields_set:
            if getattr(self, field_name) is None:
                raise ValueError(
                    "Brand-safety update fields cannot be null."
                )

        return self


class BrandSafetyRuleResponse(BaseModel):
    """
    Public representation of a stored brand-safety rule.
    """

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
    )

    id: str
    tenant_id: str
    rule_id: str

    category: BrandSafetyRuleCategory
    match_type: BrandSafetyMatchType

    pattern: str
    severity: GuardrailSeverity

    active: bool
    case_sensitive: bool

    created_by: str
    updated_by: str | None

    created_at: datetime
    updated_at: datetime