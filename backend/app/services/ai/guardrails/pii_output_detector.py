"""
pii_output_detector.py

PII detection for AI-generated FieldOps communication.

The detector scans the AI response before placeholder restoration.

It detects newly generated:

- Email addresses
- Phone numbers
- Social Security numbers
- Street addresses
- GPS coordinates

The detector never stores the detected private value in a
GuardrailViolation. Only audit-safe categories and counts are
returned.

This detector:

- Does not modify CommunicationContext
- Does not modify CommunicationDecision
- Does not restore placeholders
- Does not render fallback templates
- Does not send notifications
"""

from __future__ import annotations

import re

from enum import StrEnum
from time import perf_counter
from typing import Final

from app.services.ai.FieldOpsAI.schemas.communication import (
    CommunicationContext,
    CommunicationDecision,
)
from app.services.ai.guardrails.contracts import (
    GuardrailCategory,
    GuardrailCheckResult,
    GuardrailField,
    GuardrailSeverity,
    GuardrailViolation,
)


class PIIOutputType(StrEnum):
    """
    Supported PII types detected in generated communication.
    """

    EMAIL = "EMAIL"
    PHONE = "PHONE"
    SSN = "SSN"
    ADDRESS = "ADDRESS"
    GPS = "GPS"


class PIIOutputDetector:
    """
    Detect newly generated private information in AI output.
    """

    checker_name: Final[str] = "pii_output_detector"

    OUTPUT_FIELDS: Final[tuple[GuardrailField, ...]] = (
        "title",
        "subject",
        "message",
    )

    EMAIL_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"\b"
        r"[A-Z0-9._%+-]+"
        r"@"
        r"[A-Z0-9.-]+"
        r"\."
        r"[A-Z]{2,63}"
        r"\b",
        re.IGNORECASE,
    )

    SSN_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"(?<!\d)"
        r"\d{3}-\d{2}-\d{4}"
        r"(?!\d)"
    )

    # This finds phone-like numeric candidates.
    #
    # Additional validation is performed later by counting
    # digits and excluding identifiers such as JOB-1234567890.
    PHONE_CANDIDATE_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"(?<![A-Za-z0-9])"
        r"(?:\+?\d|\(\d)"
        r"[\d().\-\s]{7,}"
        r"\d"
        r"(?![A-Za-z0-9])"
    )

    ADDRESS_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"\b"
        r"\d{1,6}[A-Za-z]?"
        r"(?:-\d{1,6})?"
        r"\s+"
        r"(?:[A-Za-z0-9.'-]+\s+){0,6}"
        r"(?:"
        r"Street|St|Road|Rd|Avenue|Ave|Boulevard|Blvd|"
        r"Lane|Ln|Drive|Dr|Court|Ct|Circle|Cir|"
        r"Highway|Hwy|Way|Parkway|Pkwy|Terrace|Ter|"
        r"Place|Pl|Route|Sector|Block|Nagar|Colony"
        r")"
        r"\b",
        re.IGNORECASE,
    )

    # Unlabelled coordinate pairs require decimal values to
    # reduce false positives from ordinary numbers.
    GPS_PAIR_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"(?<![\d.])"
        r"[+-]?"
        r"(?:90\.0+|[0-8]?\d\.\d+)"
        r"\s*[,;]\s*"
        r"[+-]?"
        r"(?:180\.0+|(?:1[0-7]\d|[0-9]?\d)\.\d+)"
        r"(?!\d)"
        r"(?!\.\d)"
    )

    # Labelled latitude and longitude may contain integers or
    # decimal values because the labels make the meaning clear.
    GPS_LABELLED_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"\b"
        r"(?:lat|latitude)"
        r"\s*[:=]\s*"
        r"[+-]?"
        r"(?:90(?:\.0+)?|[0-8]?\d(?:\.\d+)?)"
        r"\s*[,;]?\s*"
        r"(?:lon|lng|longitude)"
        r"\s*[:=]\s*"
        r"[+-]?"
        r"(?:180(?:\.0+)?|"
        r"(?:1[0-7]\d|[0-9]?\d)(?:\.\d+)?)"
        r"\b",
        re.IGNORECASE,
    )

    VIOLATION_CODES: Final[dict[PIIOutputType, str]] = {
        PIIOutputType.EMAIL: "PII_EMAIL_DETECTED",
        PIIOutputType.PHONE: "PII_PHONE_DETECTED",
        PIIOutputType.SSN: "PII_SSN_DETECTED",
        PIIOutputType.ADDRESS: "PII_ADDRESS_DETECTED",
        PIIOutputType.GPS: "PII_GPS_DETECTED",
    }

    SAFE_MESSAGES: Final[dict[PIIOutputType, str]] = {
        PIIOutputType.EMAIL: (
            "Generated communication contains prohibited "
            "email information."
        ),
        PIIOutputType.PHONE: (
            "Generated communication contains prohibited "
            "phone information."
        ),
        PIIOutputType.SSN: (
            "Generated communication contains prohibited "
            "government identifier information."
        ),
        PIIOutputType.ADDRESS: (
            "Generated communication contains prohibited "
            "address information."
        ),
        PIIOutputType.GPS: (
            "Generated communication contains prohibited "
            "location coordinates."
        ),
    }

    def check(
        self,
        *,
        context: CommunicationContext,
        decision: CommunicationDecision,
    ) -> GuardrailCheckResult:
        """
        Scan generated title, subject, and message for PII.

        The context parameter is accepted because every guardrail
        follows the same GuardrailChecker interface.

        The current detector scans only the generated decision.
        It must run before placeholder restoration.

        Returns
        -------
        GuardrailCheckResult
            passed=True when no generated PII is detected.

            passed=False when one or more PII categories are
            detected.
        """

        from app.services.ai.FieldOpsAI.schemas.communication import output_text_for_validation

        started_at = perf_counter()

        violations: list[GuardrailViolation] = []

        validation_text = output_text_for_validation(decision.output)
        
        if validation_text:
            detections = self._detect_pii(validation_text)

            for pii_type, match_count in detections.items():
                if match_count == 0:
                    continue

                violations.append(
                    GuardrailViolation(
                        code=self.VIOLATION_CODES[pii_type],
                        category=GuardrailCategory.PII,
                        severity=GuardrailSeverity.CRITICAL,
                        message=self.SAFE_MESSAGES[pii_type],
                        field="output",  # Generic field for combined output
                        safe_metadata={
                            "pii_type": pii_type.value,
                            "match_count": match_count,
                        },
                    )
                )

        latency_ms = (perf_counter() - started_at) * 1000

        return GuardrailCheckResult(
            checker_name=self.checker_name,
            passed=not violations,
            violations=tuple(violations),
            latency_ms=latency_ms,
        )

    @classmethod
    def _detect_pii(
        cls,
        value: str,
    ) -> dict[PIIOutputType, int]:
        """
        Detect supported PII categories in one string.

        Only counts are returned. The detected values themselves
        are never returned or stored.

        Parameters
        ----------
        value
            Generated title, subject, or message.

        Returns
        -------
        dict[PIIOutputType, int]
            Match count for every detected PII type.
        """

        return {
            PIIOutputType.EMAIL: len(
                cls.EMAIL_PATTERN.findall(value)
            ),
            PIIOutputType.PHONE: cls._count_phone_numbers(
                value
            ),
            PIIOutputType.SSN: len(
                cls.SSN_PATTERN.findall(value)
            ),
            PIIOutputType.ADDRESS: len(
                cls.ADDRESS_PATTERN.findall(value)
            ),
            PIIOutputType.GPS: cls._count_gps_coordinates(
                value
            ),
        }

    @classmethod
    def _count_phone_numbers(
        cls,
        value: str,
    ) -> int:
        """
        Count likely phone numbers.

        A candidate is considered a phone number when:

        - It contains between 10 and 15 digits
        - It is not an SSN
        - It is not immediately associated with a known
          operational identifier label such as JOB or TICKET

        This combines regex matching with simple heuristics to
        reduce false positives.
        """

        count = 0

        for match in cls.PHONE_CANDIDATE_PATTERN.finditer(value):
            candidate = match.group(0).strip()

            digit_count = sum(
                character.isdigit()
                for character in candidate
            )

            if not 10 <= digit_count <= 15:
                continue

            if cls.SSN_PATTERN.fullmatch(candidate):
                continue

            if cls._has_identifier_prefix(
                value=value,
                match_start=match.start(),
            ):
                continue

            count += 1

        return count

    @staticmethod
    def _has_identifier_prefix(
        *,
        value: str,
        match_start: int,
    ) -> bool:
        """
        Return True when a numeric candidate follows a known ID
        label.

        Examples excluded from phone detection:

        - JOB-1234567890
        - TICKET: 1234567890
        - ORDER_1234567890
        - REF 1234567890
        """

        prefix = value[
            max(0, match_start - 20):match_start
        ]

        identifier_prefix_pattern = re.compile(
            r"(?:"
            r"job|ticket|case|order|reference|ref"
            r")"
            r"[\s_:#-]*$",
            re.IGNORECASE,
        )

        return (
            identifier_prefix_pattern.search(prefix)
            is not None
        )

    @classmethod
    def _count_gps_coordinates(
        cls,
        value: str,
    ) -> int:
        """
        Count GPS coordinate pairs.

        Both formats are supported:

        - 37.7749, -122.4194
        - latitude: 37.7749 longitude: -122.4194
        """

        unlabelled_count = len(
            cls.GPS_PAIR_PATTERN.findall(value)
        )

        labelled_count = len(
            cls.GPS_LABELLED_PATTERN.findall(value)
        )

        return unlabelled_count + labelled_count    