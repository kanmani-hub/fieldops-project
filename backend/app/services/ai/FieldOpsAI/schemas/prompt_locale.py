from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PromptLanguage(str, Enum):
    """
    Authoritative supported languages for managed prompts.
    """

    en = "en"
    es = "es"
    ta = "ta"
    hi = "hi"


class TranslationCompletenessIssue(BaseModel):
    """
    Describes an issue with a specific language variant.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    language: PromptLanguage
    issue_code: str
    details: str | None = None


class TemplateTranslationCompleteness(BaseModel):
    """
    Tenant-scoped completeness report for a single template family.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    agent_type: str
    channel: str
    status: str
    
    available_languages: list[PromptLanguage] = Field(default_factory=list)
    missing_languages: list[PromptLanguage] = Field(default_factory=list)
    invalid_languages: list[PromptLanguage] = Field(default_factory=list)
    
    issues: list[TranslationCompletenessIssue] = Field(default_factory=list)
    
    is_complete: bool
    template_ids: dict[PromptLanguage, int] = Field(default_factory=dict)
    versions: dict[PromptLanguage, int] = Field(default_factory=dict)


class TranslationCompletenessResult(BaseModel):
    """
    Paginated completeness response for an entire tenant or platform.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    items: list[TemplateTranslationCompleteness]
    total_families: int
    complete_families: int
    incomplete_families: int
