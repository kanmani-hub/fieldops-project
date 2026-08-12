"""
pii_sanitizer.py

Privacy-by-design sanitization for FieldOps AI.

Purpose
-------
Ensures personally identifiable information does not leave
the FieldOps backend and reach Groq or another external
AI provider.

The sanitizer:

- Replaces known PII fields with {{variable_name}} placeholders.
- Handles nested dictionaries, lists, and Pydantic models.
- Detects PII inside free-text values using regex patterns.
- Maintains a reversible, request-scoped mapping in memory.
- Restores original values locally after AI processing.
- Validates that high-confidence PII does not remain in prompts.

Important
---------
Placeholder mappings are never persisted. Each request receives
its own PlaceholderMap instance, making the sanitizer safe for
concurrent and multi-tenant requests.
"""

from __future__ import annotations

import copy,json,re

from enum import Enum
from typing import Any, Mapping,Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
)


# ==========================================================
# PII Categories
# ==========================================================


class PIICategory(str, Enum):
    """
    Supported categories of personally identifiable data.
    """

    CUSTOMER_NAME = "customer_name"
    TECHNICIAN_NAME = "technician_name"
    PHONE = "phone"
    EMAIL = "email"
    ADDRESS = "address"
    JOB_ID = "job_id"
    LOCATION = "location"
    SSN = "ssn"
    NESTED_FIELD = "nested_field"


# ==========================================================
# Exceptions
# ==========================================================


class PIILeakageError(ValueError):
    """
    Raised when unsanitized PII remains in a prompt.

    The exception contains only category names. It does not
    include the original sensitive value.
    """

    def __init__(
        self,
        categories: set[PIICategory],
    ) -> None:
        self.categories = categories

        category_names = ", ".join(
            sorted(
                category.value
                for category in categories
            )
        )

        super().__init__(
            "Prompt validation failed because possible "
            f"PII remains. Categories: {category_names}"
        )


# ==========================================================
# Placeholder Map
# ==========================================================


class PlaceholderMap(BaseModel):
    """
    In-memory mapping between placeholders and real values.

    Example
    -------
    {
        "{{customer_name}}": "John",
        "{{customer_phone}}": "9876543210"
    }

    The mapping belongs to one request only and must never
    be written to Redis, PostgreSQL, application logs, or
    an external provider.
    """

    model_config = ConfigDict(
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    values: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Placeholder-to-original-value mapping."
        ),
    )

    categories: dict[str, PIICategory] = Field(
        default_factory=dict,
        description=(
            "PII category associated with each placeholder."
        ),
    )

    # Used only at runtime to reuse an existing placeholder
    # when the same value appears more than once.
    _reverse_index: dict[
        tuple[str, str],
        str,
    ] = PrivateAttr(
        default_factory=dict
    )

    # ------------------------------------------------------

    def add(
        self,
        *,
        preferred_name: str,
        category: PIICategory,
        value: Any,
    ) -> str:
        """
        Add a value and return its placeholder.

        When the same category and value appear repeatedly,
        the existing placeholder is reused.
        """

        normalized_name = self._normalize_name(
            preferred_name
        )

        fingerprint = self._fingerprint(
            value
        )

        reverse_key = (
            category.value,
            fingerprint,
        )

        existing_placeholder = self._reverse_index.get(
            reverse_key
        )

        if existing_placeholder is not None:
            return existing_placeholder

        candidate_name = normalized_name
        suffix = 2

        placeholder = (
            f"{{{{{candidate_name}}}}}"
        )

        while placeholder in self.values:
            existing_value = self.values[
                placeholder
            ]

            if existing_value == value:
                self._reverse_index[
                    reverse_key
                ] = placeholder

                return placeholder

            candidate_name = (
                f"{normalized_name}_{suffix}"
            )

            placeholder = (
                f"{{{{{candidate_name}}}}}"
            )

            suffix += 1

        self.values[placeholder] = copy.deepcopy(
            value
        )

        self.categories[placeholder] = category

        self._reverse_index[
            reverse_key
        ] = placeholder

        return placeholder

    # ------------------------------------------------------

    def clear(self) -> None:
        """
        Remove all request-scoped sensitive values.
        """

        self.values.clear()
        self.categories.clear()
        self._reverse_index.clear()

    # ------------------------------------------------------

    def __len__(self) -> int:
        return len(self.values)

    # ------------------------------------------------------

    @staticmethod
    def _normalize_name(
        name: str,
    ) -> str:
        """
        Convert a value into valid placeholder-name format.
        """

        normalized = re.sub(
            r"[^a-z0-9_]+",
            "_",
            name.strip().lower(),
        )

        normalized = re.sub(
            r"_+",
            "_",
            normalized,
        ).strip("_")

        if not normalized:
            normalized = "pii_value"

        if normalized[0].isdigit():
            normalized = f"value_{normalized}"

        return normalized

    # ------------------------------------------------------

    @staticmethod
    def _fingerprint(
        value: Any,
    ) -> str:
        """
        Create a stable in-memory fingerprint.

        This fingerprint is used only inside the current
        request and is not stored externally.
        """

        try:
            return json.dumps(
                value,
                sort_keys=True,
                ensure_ascii=False,
                default=str,
            )

        except (TypeError, ValueError):
            return repr(value)


# ==========================================================
# Sanitization Result
# ==========================================================


class SanitizationResult(BaseModel):
    """
    Result produced by PIISanitizer.sanitize().
    """

    model_config = ConfigDict(
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    sanitized_data: Any

    placeholder_map: PlaceholderMap

    replacement_count: int = Field(
        ge=0,
    )

# ==========================================================
# Local Named-Entity Recognition
# ==========================================================


class DetectedNameEntity(BaseModel):
    """
    A person-name entity found in unstructured text.

    The recognizer returns text positions so the sanitizer
    can replace the original value without exposing it.
    """

    model_config = ConfigDict(
        extra="forbid",
    )

    start: int = Field(
        ge=0,
    )

    end: int = Field(
        gt=0,
    )

    text: str = Field(
        min_length=1,
    )

    category: PIICategory

    placeholder_name: str = Field(
        min_length=1,
    )


class NamedEntityRecognizer(Protocol):
    """
    Contract for local named-entity recognition.

    A future local spaCy or Presidio implementation can
    implement this interface without changing PIISanitizer.
    """

    def detect(
        self,
        text: str,
    ) -> list[DetectedNameEntity]:
        """
        Detect person-name entities in unstructured text.
        """

        ...


class ContextualNameRecognizer:
    """
    Deterministic local recognizer for FieldOps person names.

    It detects names only when they appear near strong FieldOps
    context such as:

    - customer john doe
    - customer named john doe
    - technician kumar raj
    - assigned technician kumar raj
    - mr. arun kumar    

    Restricting detection to strong context reduces false
    positives compared with replacing every capitalized phrase.
    """

    NAME_TOKEN = (
        r"(?:"
        r"[A-Z][A-Za-z'’\-]+"
        r"|"
        r"[A-Z]\."
        r")"
    )

    FULL_NAME = (
        NAME_TOKEN
        + r"(?:\s+"
        + NAME_TOKEN
        + r"){1,3}"
    )

    CUSTOMER_PATTERN = re.compile(
        (
            r"\b"
            r"(?i:customer|client|caller)"
            r"\b"
            r"(?:\s+(?i:name|named))?"
            r"\s*"
            r"(?:(?i:is)\s+|[:\-]\s*)?"
            r"(?P<name>"
            + FULL_NAME
            + r")"
            r"\b"
        )
    )

    TECHNICIAN_PATTERN = re.compile(
        (
            r"\b"
            r"(?i:"
            r"technician|engineer|"
            r"field\s+technician|"
            r"assigned\s+technician"
            r")"
            r"\b"
            r"(?:\s+(?i:name|named))?"
            r"\s*"
            r"(?:(?i:is)\s+|[:\-]\s*)?"
            r"(?P<name>"
            + FULL_NAME
            + r")"
            r"\b"
        )
    )

    TITLED_PERSON_PATTERN = re.compile(
        (
            r"\b"
            r"(?i:mr|mrs|ms|miss|dr)"
            r"\.?"
            r"\s+"
            r"(?P<name>"
            + FULL_NAME
            + r")"
            r"\b"
        )
    )

    BLOCKED_PHRASES = {
        "customer service",
        "service request",
        "service team",
        "support team",
        "support center",
        "service center",
        "field operations",
        "fieldops commander",
    }

    # ------------------------------------------------------

    def detect(
        self,
        text: str,
    ) -> list[DetectedNameEntity]:
        """
        Detect contextual person names.

        Overlapping matches are deduplicated. Returned entities
        are ordered by their location in the original text.
        """

        if not isinstance(
            text,
            str,
        ):
            raise TypeError(
                "NER input must be a string."
            )

        entities: list[
            DetectedNameEntity
        ] = []

        occupied_spans: set[
            tuple[int, int]
        ] = set()

        rules = (
            (
                self.CUSTOMER_PATTERN,
                PIICategory.CUSTOMER_NAME,
                "customer_name",
            ),
            (
                self.TECHNICIAN_PATTERN,
                PIICategory.TECHNICIAN_NAME,
                "technician_name",
            ),
            (
                self.TITLED_PERSON_PATTERN,
                PIICategory.NESTED_FIELD,
                "detected_person_name",
            ),
        )

        for (
            pattern,
            category,
            placeholder_name,
        ) in rules:
            for match in pattern.finditer(
                text
            ):
                entity_text = match.group(
                    "name"
                ).strip()

                normalized_entity = (
                    " ".join(
                        entity_text.lower().split()
                    )
                )

                if (
                    normalized_entity
                    in self.BLOCKED_PHRASES
                ):
                    continue

                span = match.span(
                    "name"
                )

                if span in occupied_spans:
                    continue

                if self._overlaps_existing(
                    span=span,
                    occupied_spans=occupied_spans,
                ):
                    continue

                occupied_spans.add(
                    span
                )

                entities.append(
                    DetectedNameEntity(
                        start=span[0],
                        end=span[1],
                        text=entity_text,
                        category=category,
                        placeholder_name=(
                            placeholder_name
                        ),
                    )
                )

        return sorted(
            entities,
            key=lambda entity: entity.start,
        )

    # ------------------------------------------------------

    @staticmethod
    def _overlaps_existing(
        *,
        span: tuple[int, int],
        occupied_spans: set[
            tuple[int, int]
        ],
    ) -> bool:
        """
        Return True when a candidate overlaps another entity.
        """

        start, end = span

        return any(
            start < existing_end
            and end > existing_start
            for (
                existing_start,
                existing_end,
            ) in occupied_spans
        )

# ==========================================================
# PII Sanitizer
# ==========================================================


class PIISanitizer:
    """
    Stateless FieldOps PII sanitizer.

    The sanitizer itself holds no customer data. Sensitive
    values are stored only in the PlaceholderMap returned
    for an individual request.
    """
    def __init__(
        self,
        name_recognizer: (
            NamedEntityRecognizer | None
        ) = None,
    ) -> None:
        """
        Initialize the stateless sanitizer.

        The default recognizer performs local contextual
        person-name recognition. A different local NER
        implementation can be injected when needed.
        """

        self.name_recognizer = (
            name_recognizer
            if name_recognizer is not None
            else ContextualNameRecognizer()
        )    

    PLACEHOLDER_PATTERN = re.compile(
        r"\{\{[a-z][a-z0-9_]*\}\}"
    )

    EMAIL_PATTERN = re.compile(
        r"\b[A-Za-z0-9._%+-]+"
        r"@[A-Za-z0-9.-]+"
        r"\.[A-Za-z]{2,}\b"
    )

    SSN_PATTERN = re.compile(
        r"(?<!\d)"
        r"\d{3}[- ]\d{2}[- ]\d{4}"
        r"(?!\d)"
    )

    PHONE_PATTERN = re.compile(
        r"(?<![\w\d])"
        r"(?:\+?\d{1,3}[\s.-]?)?"
        r"(?:\(?\d{2,4}\)?[\s.-]?)?"
        r"\d{3,4}[\s.-]?\d{4}"
        r"(?![\w\d])"
    )

    GPS_PAIR_PATTERN = re.compile(
        r"(?<![\d.])"
        r"-?(?:"
        r"90(?:\.0{3,})?"
        r"|"
        r"(?:[0-8]?\d)\.\d{3,}"
        r")"
        r"\s*,\s*"
        r"-?(?:"
        r"180(?:\.0{3,})?"
        r"|"
        r"(?:1[0-7]\d|[0-9]?\d)\.\d{3,}"
        r")"
        r"(?!\d|\.\d)"
    )

    JOB_ID_PATTERN = re.compile(
        r"\b"
        r"(?:JOB|WO|WORK[\s_-]?ORDER)"
        r"[\s_:#-]*"
        r"(?=[A-Z0-9_-]*\d)"
        r"[A-Z0-9]+"
        r"(?:[_-][A-Z0-9]+)*"
        r"\b",
        re.IGNORECASE,
    )

    ADDRESS_PATTERN = re.compile(
        r"\b"
        r"\d{1,6}\s+"
        r"(?:[A-Za-z0-9.'-]+\s+){0,5}"
        r"(?:"
        r"Street|St|Road|Rd|Avenue|Ave|"
        r"Boulevard|Blvd|Lane|Ln|Drive|Dr|"
        r"Court|Ct|Way|Highway|Hwy"
        r")"
        r"\b",
        re.IGNORECASE,
    )

    CUSTOMER_NAME_KEYS = {
        "customer_name",
        "customer_full_name",
    }

    TECHNICIAN_NAME_KEYS = {
        "technician_name",
        "technician_full_name",
        "tech_name",
    }

    PHONE_KEYS = {
        "customer_phone",
        "technician_phone",
        "phone",
        "phone_number",
        "mobile",
        "mobile_number",
        "contact_number",
    }

    EMAIL_KEYS = {
        "customer_email",
        "technician_email",
        "email",
        "email_address",
    }

    ADDRESS_KEYS = {
        "service_address",
        "customer_address",
        "address",
        "street_address",
    }

    ADDRESS_COMPONENT_KEYS = {
        "street",
        "street_1",
        "street_2",
        "address_line_1",
        "address_line_2",
        "city",
        "state",
        "postal_code",
        "postcode",
        "zip",
        "zip_code",
        "country",
    }

    JOB_ID_KEYS = {
        "job_id",
        "work_order_id",
        "workorder_id",
    }

    LOCATION_KEYS = {
        "technician_location",
        "gps",
        "gps_coordinates",
        "coordinates",
    }

    LATITUDE_KEYS = {
        "latitude",
        "lat",
    }

    LONGITUDE_KEYS = {
        "longitude",
        "lng",
        "lon",
    }

    # ------------------------------------------------------

    def sanitize(
        self,
        data: Any,
    ) -> SanitizationResult:
        """
        Sanitize structured or unstructured data.

        Parameters
        ----------
        data
            Dictionary, list, string, Pydantic model,
            or another JSON-compatible value.

        Returns
        -------
        SanitizationResult
            Sanitized data and the in-memory placeholder map.
        """

        placeholder_map = PlaceholderMap()

        sanitized_data = self._sanitize_value(
            value=data,
            path=(),
            placeholder_map=placeholder_map,
        )

        return SanitizationResult(
            sanitized_data=sanitized_data,
            placeholder_map=placeholder_map,
            replacement_count=len(
                placeholder_map
            ),
        )

    # ------------------------------------------------------

    def sanitize_prompt(
        self,
        prompt: str,
        placeholder_map: PlaceholderMap | None = None,
    ) -> tuple[str, PlaceholderMap]:
        """
        Sanitize a final text prompt before provider use.

        This performs a second safety scan after structured
        context has already been converted into prompt text.
        """

        if not isinstance(
            prompt,
            str,
        ):
            raise TypeError(
                "Prompt must be a string."
            )

        active_map = (
            placeholder_map
            if placeholder_map is not None
            else PlaceholderMap()
        )

        sanitized_prompt = self._sanitize_text(
            text=prompt,
            placeholder_map=active_map,
        )

        self.validate_no_pii(
            sanitized_prompt
        )

        return (
            sanitized_prompt,
            active_map,
        )

    # ------------------------------------------------------

    def restore_data(
        self,
        data: Any,
        placeholder_map: PlaceholderMap,
        *,
        clear_mapping: bool = True,
    ) -> Any:
        """
        Restore original values locally.

        By default, the in-memory mapping is cleared after
        restoration so sensitive data does not remain in
        memory longer than necessary.
        """

        if not isinstance(
            placeholder_map,
            PlaceholderMap,
        ):
            raise TypeError(
                "placeholder_map must be a PlaceholderMap."
            )

        try:
            return self._restore_value(
                value=data,
                placeholder_map=placeholder_map,
            )

        finally:
            if clear_mapping:
                placeholder_map.clear()

    # ------------------------------------------------------

    def restore(
        self,
        data: Any,
        placeholder_map: PlaceholderMap,
        *,
        clear_mapping: bool = True,
    ) -> Any:
        """
        Alias for restore_data().
        """

        return self.restore_data(
            data=data,
            placeholder_map=placeholder_map,
            clear_mapping=clear_mapping,
        )

    # ------------------------------------------------------

    def validate_no_pii(
        self,
        text: str,
    ) -> None:
        """
        Verify that high-confidence PII does not remain.

        Raises
        ------
        PIILeakageError
            When possible PII remains in the text.
        """

        categories = self.detect_remaining_pii(
            text
        )

        if categories:
            raise PIILeakageError(
                categories
            )

    # ------------------------------------------------------

    def detect_remaining_pii(
        self,
        text: str,
    ) -> set[PIICategory]:
        """
        Return PII categories still detected in text.

        Real values are deliberately not returned to prevent
        them from entering logs or exception messages.
        """

        if not isinstance(
            text,
            str,
        ):
            raise TypeError(
                "Text must be a string."
            )

        # Ignore valid placeholder tokens during detection.
        searchable_text = self.PLACEHOLDER_PATTERN.sub(
            " ",
            text,
        )

        detected: set[PIICategory] = set()

        detection_patterns = (
            (
                PIICategory.EMAIL,
                self.EMAIL_PATTERN,
            ),
            (
                PIICategory.SSN,
                self.SSN_PATTERN,
            ),
            (
                PIICategory.LOCATION,
                self.GPS_PAIR_PATTERN,
            ),
            (
                PIICategory.ADDRESS,
                self.ADDRESS_PATTERN,
            ),
            (
                PIICategory.PHONE,
                self.PHONE_PATTERN,
            ),
            (
                PIICategory.JOB_ID,
                self.JOB_ID_PATTERN,
            ),
        )

        for category, pattern in detection_patterns:
            if pattern.search(
                searchable_text
            ):
                detected.add(
                    category
                )

        # Local NER validation catches contextual person names
        # that are not covered by regex patterns.
        for entity in self.name_recognizer.detect(
            searchable_text
        ):
            detected.add(
                entity.category
            )

        return detected
    # ======================================================
    # Recursive Sanitization
    # ======================================================

    def _sanitize_value(
        self,
        *,
        value: Any,
        path: tuple[str, ...],
        placeholder_map: PlaceholderMap,
    ) -> Any:
        """
        Recursively sanitize a value.
        """

        if isinstance(
            value,
            BaseModel,
        ):
            value = value.model_dump(
                mode="python"
            )

        if isinstance(
            value,
            Mapping,
        ):
            sanitized_mapping: dict[
                str,
                Any,
            ] = {}

            for key, nested_value in value.items():
                string_key = str(
                    key
                )

                classification = self._classify_field(
                    path=path,
                    key=string_key,
                )

                if classification is not None:
                    (
                        category,
                        placeholder_name,
                    ) = classification

                    # Preserve nested shape for structured
                    # addresses and GPS objects.
                    if isinstance(
                        nested_value,
                        (Mapping, list, tuple),
                    ):
                        sanitized_mapping[
                            string_key
                        ] = self._sanitize_value(
                            value=nested_value,
                            path=path + (
                                string_key,
                            ),
                            placeholder_map=(
                                placeholder_map
                            ),
                        )

                    else:
                        sanitized_mapping[
                            string_key
                        ] = placeholder_map.add(
                            preferred_name=(
                                placeholder_name
                            ),
                            category=category,
                            value=nested_value,
                        )

                else:
                    sanitized_mapping[
                        string_key
                    ] = self._sanitize_value(
                        value=nested_value,
                        path=path + (
                            string_key,
                        ),
                        placeholder_map=placeholder_map,
                    )

            return sanitized_mapping

        if isinstance(
            value,
            list,
        ):
            return [
                self._sanitize_value(
                    value=item,
                    path=path,
                    placeholder_map=placeholder_map,
                )
                for item in value
            ]

        if isinstance(
            value,
            tuple,
        ):
            return tuple(
                self._sanitize_value(
                    value=item,
                    path=path,
                    placeholder_map=placeholder_map,
                )
                for item in value
            )

        if isinstance(
            value,
            str,
        ):
            return self._sanitize_text(
                text=value,
                placeholder_map=placeholder_map,
            )

        return value

    # ------------------------------------------------------

    def _sanitize_text(
        self,
        *,
        text: str,
        placeholder_map: PlaceholderMap,
    ) -> str:
        """
        Replace PII found inside free text.

        Sanitization order:

        1. Reuse known structured-value placeholders.
        2. Apply high-confidence regex detection.
        3. Apply local contextual name recognition.
        """

        sanitized = self._replace_known_values(
            text=text,
            placeholder_map=placeholder_map,
        )

        patterns = (
            (
                PIICategory.EMAIL,
                "detected_email",
                self.EMAIL_PATTERN,
            ),
            (
                PIICategory.SSN,
                "detected_ssn",
                self.SSN_PATTERN,
            ),
            (
                PIICategory.LOCATION,
                "detected_location",
                self.GPS_PAIR_PATTERN,
            ),
            (
                PIICategory.ADDRESS,
                "detected_address",
                self.ADDRESS_PATTERN,
            ),
            (
                PIICategory.PHONE,
                "detected_phone",
                self.PHONE_PATTERN,
            ),
            (
                PIICategory.JOB_ID,
                "detected_job_id",
                self.JOB_ID_PATTERN,
            ),
        )

        for (
            category,
            placeholder_name,
            pattern,
        ) in patterns:

            def replacement(
                match: re.Match[str],
                *,
                current_category: PIICategory = (
                    category
                ),
                current_name: str = (
                    placeholder_name
                ),
            ) -> str:
                return placeholder_map.add(
                    preferred_name=current_name,
                    category=current_category,
                    value=match.group(0),
                )

            sanitized = pattern.sub(
                replacement,
                sanitized,
            )

        sanitized = self._sanitize_named_entities(
            text=sanitized,
            placeholder_map=placeholder_map,
        )

        return sanitized
    # ------------------------------------------------------

    def _replace_known_values(
        self,
        *,
        text: str,
        placeholder_map: PlaceholderMap,
    ) -> str:
        """
        Replace already-known structured PII values when they
        are repeated inside unstructured text.

        Example
        -------
        Structured field:
            customer_name = "Ruby Devi"

        Free-text field:
            "Ruby Devi requested an update."

        Both occurrences reuse {{customer_name}}.
        """

        sanitized = text

        candidates: list[
            tuple[int, str, str, PIICategory]
        ] = []

        for (
            placeholder,
            original_value,
        ) in placeholder_map.values.items():
            if not isinstance(
                original_value,
                str,
            ):
                continue

            original_text = (
                original_value.strip()
            )

            if not original_text:
                continue

            category = (
                placeholder_map.categories[
                    placeholder
                ]
            )

            minimum_length = (
                4
                if category
                == PIICategory.NESTED_FIELD
                else 2
            )

            if (
                len(original_text)
                < minimum_length
            ):
                continue

            candidates.append(
                (
                    len(original_text),
                    placeholder,
                    original_text,
                    category,
                )
            )

        # Longer values are replaced first to avoid partial
        # replacement when one value contains another.
        candidates.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        for (
            _,
            placeholder,
            original_text,
            _category,
        ) in candidates:
            value_pattern = re.compile(
                (
                    r"(?<!\w)"
                    + re.escape(original_text)
                    + r"(?!\w)"
                ),
                re.IGNORECASE,
            )

            sanitized = value_pattern.sub(
                lambda _match, token=placeholder: token,
                sanitized,
            )

        return sanitized

    # ------------------------------------------------------

    def _sanitize_named_entities(
        self,
        *,
        text: str,
        placeholder_map: PlaceholderMap,
    ) -> str:
        """
        Replace locally detected person-name entities.

        Replacements are performed from right to left so the
        original character positions remain valid.
        """

        entities = self.name_recognizer.detect(
            text
        )

        sanitized = text

        for entity in sorted(
            entities,
            key=lambda item: item.start,
            reverse=True,
        ):
            placeholder = placeholder_map.add(
                preferred_name=(
                    entity.placeholder_name
                ),
                category=entity.category,
                value=entity.text,
            )

            sanitized = (
                sanitized[:entity.start]
                + placeholder
                + sanitized[entity.end:]
            )

        return sanitized
    # ======================================================
    # Restoration
    # ======================================================

    def _restore_value(
        self,
        *,
        value: Any,
        placeholder_map: PlaceholderMap,
    ) -> Any:
        """
        Recursively restore placeholders.
        """

        if isinstance(
            value,
            Mapping,
        ):
            return {
                key: self._restore_value(
                    value=nested_value,
                    placeholder_map=placeholder_map,
                )
                for key, nested_value
                in value.items()
            }

        if isinstance(
            value,
            list,
        ):
            return [
                self._restore_value(
                    value=item,
                    placeholder_map=placeholder_map,
                )
                for item in value
            ]

        if isinstance(
            value,
            tuple,
        ):
            return tuple(
                self._restore_value(
                    value=item,
                    placeholder_map=placeholder_map,
                )
                for item in value
            )

        if not isinstance(
            value,
            str,
        ):
            return value

        # When the complete value is a placeholder, restore
        # the original Python type rather than forcing it
        # into a string.
        if value in placeholder_map.values:
            return copy.deepcopy(
                placeholder_map.values[
                    value
                ]
            )

        restored = value

        for (
            placeholder,
            original_value,
        ) in placeholder_map.values.items():
            restored = restored.replace(
                placeholder,
                str(original_value),
            )

        return restored

    # ======================================================
    # Field Classification
    # ======================================================

    def _classify_field(
        self,
        *,
        path: tuple[str, ...],
        key: str,
    ) -> tuple[
        PIICategory,
        str,
    ] | None:
        """
        Classify a structured field by its path and name.
        """

        normalized_key = self._normalize_key(
            key
        )

        path_tokens = self._path_tokens(
            path
        )

        if normalized_key in self.CUSTOMER_NAME_KEYS:
            return (
                PIICategory.CUSTOMER_NAME,
                "customer_name",
            )

        if normalized_key in self.TECHNICIAN_NAME_KEYS:
            return (
                PIICategory.TECHNICIAN_NAME,
                "technician_name",
            )

        if normalized_key == "name":
            if "customer" in path_tokens:
                return (
                    PIICategory.CUSTOMER_NAME,
                    "customer_name",
                )

            if (
                "technician" in path_tokens
                or "tech" in path_tokens
            ):
                return (
                    PIICategory.TECHNICIAN_NAME,
                    "technician_name",
                )

        if normalized_key in self.PHONE_KEYS:
            placeholder_name = (
                "technician_phone"
                if (
                    "technician" in path_tokens
                    or "tech" in path_tokens
                    or normalized_key
                    == "technician_phone"
                )
                else "customer_phone"
            )

            return (
                PIICategory.PHONE,
                placeholder_name,
            )

        if normalized_key in self.EMAIL_KEYS:
            placeholder_name = (
                "technician_email"
                if (
                    "technician" in path_tokens
                    or "tech" in path_tokens
                    or normalized_key
                    == "technician_email"
                )
                else "customer_email"
            )

            return (
                PIICategory.EMAIL,
                placeholder_name,
            )

        if normalized_key in self.JOB_ID_KEYS:
            return (
                PIICategory.JOB_ID,
                "job_id",
            )

        if (
            normalized_key == "id"
            and "job" in path_tokens
        ):
            return (
                PIICategory.JOB_ID,
                "job_id",
            )

        if normalized_key in self.ADDRESS_KEYS:
            placeholder_name = (
                "customer_address"
                if "customer" in path_tokens
                else "service_address"
            )

            return (
                PIICategory.ADDRESS,
                placeholder_name,
            )

        if (
            normalized_key
            in self.ADDRESS_COMPONENT_KEYS
            and (
                "address" in path_tokens
                or "service_address" in path_tokens
                or "customer_address" in path_tokens
            )
        ):
            prefix = (
                "customer"
                if "customer" in path_tokens
                else "service"
            )

            return (
                PIICategory.NESTED_FIELD,
                f"{prefix}_{normalized_key}",
            )

        if normalized_key in self.LOCATION_KEYS:
            return (
                PIICategory.LOCATION,
                "technician_location",
            )

        if normalized_key in self.LATITUDE_KEYS:
            if (
                "technician" in path_tokens
                or "location" in path_tokens
                or "technician_location"
                in path_tokens
            ):
                return (
                    PIICategory.LOCATION,
                    "technician_latitude",
                )

        if normalized_key in self.LONGITUDE_KEYS:
            if (
                "technician" in path_tokens
                or "location" in path_tokens
                or "technician_location"
                in path_tokens
            ):
                return (
                    PIICategory.LOCATION,
                    "technician_longitude",
                )

        return None

    # ------------------------------------------------------

    @staticmethod
    def _normalize_key(
        key: str,
    ) -> str:
        """
        Normalize dictionary field names.
        """

        normalized = re.sub(
            r"([a-z0-9])([A-Z])",
            r"\1_\2",
            key,
        )

        normalized = re.sub(
            r"[^a-zA-Z0-9]+",
            "_",
            normalized,
        )

        return normalized.strip(
            "_"
        ).lower()

    # ------------------------------------------------------

    def _path_tokens(
        self,
        path: tuple[str, ...],
    ) -> set[str]:
        """
        Convert a nested object path into searchable tokens.
        """

        tokens: set[str] = set()

        for segment in path:
            normalized_segment = self._normalize_key(
                segment
            )

            tokens.add(
                normalized_segment
            )

            tokens.update(
                token
                for token
                in normalized_segment.split("_")
                if token
            )

        return tokens


# Shared stateless instance.
#
# Request-specific PII is not stored on this singleton.
pii_sanitizer = PIISanitizer()