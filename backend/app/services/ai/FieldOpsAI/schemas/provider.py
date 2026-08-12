"""
provider.py

Pydantic schemas and enums for FieldOps AI providers.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Self
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProviderHealth(str, Enum):
    """
    Possible health states for an AI provider.
    """

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"


class ProviderConfig(BaseModel):
    """
    Validated immutable configuration for an AI provider.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    provider_name: str
    model_name: str
    api_key_env: str
    timeout_seconds: float
    max_tokens: int
    temperature: float
    max_retries: int

    @field_validator("provider_name", "model_name")
    @classmethod
    def validate_non_blank_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Name field must not be blank.")
        return v.strip()

    @field_validator("api_key_env")
    @classmethod
    def validate_api_key_env(cls, v: str) -> str:
        v_stripped = v.strip()
        if not v_stripped:
            raise ValueError("api_key_env must not be blank.")
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", v_stripped):
            raise ValueError("api_key_env must be a valid environment variable name (e.g. UPPER_CASE_NAME).")
        v_lower = v_stripped.lower()
        if v_lower.startswith("gsk_") or v_lower.startswith("sk-") or len(v_stripped) > 50:
            raise ValueError("api_key_env must contain an environment variable name, not a raw API key secret.")
        return v_stripped

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout_seconds(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("timeout_seconds must be greater than zero.")
        return v

    @field_validator("max_tokens")
    @classmethod
    def validate_max_tokens(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max_tokens must be greater than zero.")
        return v

    @field_validator("temperature")
    @classmethod
    def validate_temperature(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("temperature must be between 0.0 and 1.0 inclusive.")
        return v

    @field_validator("max_retries")
    @classmethod
    def validate_max_retries(cls, v: int) -> int:
        if v < 0:
            raise ValueError("max_retries must be non-negative.")
        return v


class UsageStats(BaseModel):
    """
    Validated usage statistics for provider tracking.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    request_count: int = Field(ge=0)
    latency_ms: float = Field(ge=0.0)
    cost_usd: float = Field(ge=0.0)

    @model_validator(mode="after")
    def validate_token_totals(self) -> Self:
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("total_tokens must be exactly equal to prompt_tokens + completion_tokens.")
        return self


class GenerationResult(BaseModel):
    """
    Standard completion result returned by BaseAIProvider.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    text: str
    provider_name: str
    model_name: str
    usage: UsageStats

    @field_validator("text", "provider_name", "model_name")
    @classmethod
    def validate_non_blank_result(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Text/Name fields must not be blank.")
        return v.strip()
