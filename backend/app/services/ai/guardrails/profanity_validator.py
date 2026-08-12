"""
profanity_validator.py

Profanity detection for AI-generated FieldOps communication.

Detection methods
-----------------
- Canonical profanity vocabulary
- Generated transposed-letter variants
- Generated repeated-letter variants
- Case normalization
- Unicode normalization
- Common leetspeak normalization
- Repeated-character normalization
- Bounded Levenshtein comparison for longer words

The validator scans:

- title
- subject
- message

It never stores:

- The generated communication
- The detected profanity term
- Customer or technician information

Only audit-safe match counts are returned.
"""

from __future__ import annotations

import re
import unicodedata

from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from time import perf_counter
from typing import Final, Literal, Self

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


# ==========================================================
# Shared Types and Constants
# ==========================================================


ProfanityMatchType = Literal[
    "lexicon",
    "fuzzy",
]


LEET_TRANSLATION: Final = str.maketrans(
    {
        "0": "o",
        "1": "i",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
        "@": "a",
        "$": "s",
    }
)


# ==========================================================
# Exceptions
# ==========================================================


class ProfanityConfigurationError(RuntimeError):
    """
    Raised when the profanity vocabulary cannot be loaded or
    does not satisfy the configured minimum size.
    """


# ==========================================================
# Profanity Lexicon
# ==========================================================


class ProfanityLexicon:
    """
    Load and prepare the English profanity vocabulary.

    The resource contains canonical terms and selected common
    variants.

    At startup, each suitable term is expanded with:

    - Adjacent-letter transpositions
    - One repeated internal character

    This provides more than 500 detectable forms without placing
    hundreds of duplicate variants inside the resource file.
    """

    DEFAULT_RESOURCE_PATH: Final[Path] = (
        Path(__file__).resolve().parent
        / "resources"
        / "profanity_en.txt"
    )

    MINIMUM_EXPANDED_TERMS: Final[int] = 500

    # Fuzzy matching is intentionally disabled for short words.
    #
    # Applying Levenshtein distance to short words such as
    # "bitch" could incorrectly classify safe words such as
    # "witch", "pitch", or "hitch".
    MINIMUM_FUZZY_TERM_LENGTH: Final[int] = 6

    # ------------------------------------------------------

    def __init__(
        self,
        terms: Iterable[str],
        *,
        minimum_expanded_terms: int = (
            MINIMUM_EXPANDED_TERMS
        ),
    ) -> None:
        """
        Build the immutable profanity vocabulary.

        Parameters
        ----------
        terms
            Canonical terms loaded from the resource.

        minimum_expanded_terms
            Required size after safe variant expansion.
        """

        canonical_terms = {
            normalized_term
            for term in terms
            if (
                normalized_term
                := self._normalize_lexicon_term(
                    term
                )
            )
        }

        if not canonical_terms:
            raise ProfanityConfigurationError(
                "Profanity vocabulary contains no valid terms."
            )

        expanded_terms: set[str] = set()

        for term in canonical_terms:
            expanded_terms.update(
                self._expand_term(
                    term
                )
            )

        if (
            len(expanded_terms)
            < minimum_expanded_terms
        ):
            raise ProfanityConfigurationError(
                "Expanded profanity vocabulary contains "
                f"{len(expanded_terms)} terms; at least "
                f"{minimum_expanded_terms} are required."
            )

        canonical_by_length: dict[
            int,
            list[str],
        ] = {}

        for term in canonical_terms:
            canonical_by_length.setdefault(
                len(term),
                [],
            ).append(
                term
            )

        self._canonical_terms = frozenset(
            canonical_terms
        )

        self._expanded_terms = frozenset(
            expanded_terms
        )

        self._canonical_by_length = {
            length: tuple(
                sorted(
                    values
                )
            )
            for length, values
            in canonical_by_length.items()
        }

    # ------------------------------------------------------

    @classmethod
    @lru_cache(
        maxsize=1
    )
    def default(
        cls,
    ) -> Self:
        """
        Load and cache the default production vocabulary.

        The resource is loaded only once per Python process,
        rather than once for every generated message.
        """

        return cls.from_file(
            cls.DEFAULT_RESOURCE_PATH
        )

    # ------------------------------------------------------

    @classmethod
    def from_file(
        cls,
        path: Path,
    ) -> Self:
        """
        Load canonical terms from a UTF-8 text file.

        Blank lines and comment lines beginning with # are
        ignored.
        """

        if not path.is_file():
            raise ProfanityConfigurationError(
                "Profanity vocabulary file was not found: "
                f"{path}"
            )

        try:
            content = path.read_text(
                encoding="utf-8"
            )
        except OSError as exc:
            raise ProfanityConfigurationError(
                "Profanity vocabulary could not be read."
            ) from exc

        terms = [
            line
            for raw_line in content.splitlines()
            if (
                line
                := raw_line.strip()
            )
            and not line.startswith(
                "#"
            )
        ]

        return cls(
            terms
        )

    # ------------------------------------------------------

    @property
    def canonical_term_count(
        self,
    ) -> int:
        """
        Return the number of canonical resource terms.
        """

        return len(
            self._canonical_terms
        )

    # ------------------------------------------------------

    @property
    def expanded_term_count(
        self,
    ) -> int:
        """
        Return the number of detectable lexicon variants.
        """

        return len(
            self._expanded_terms
        )

    # ------------------------------------------------------

    def classify(
        self,
        token: str,
    ) -> ProfanityMatchType | None:
        """
        Classify one normalized token.

        Returns
        -------
        "lexicon"
            The token exactly matches a canonical or generated
            vocabulary variant.

        "fuzzy"
            The token is within the configured Levenshtein
            distance of a longer canonical term.

        None
            No profanity was detected.
        """

        if token in self._expanded_terms:
            return "lexicon"

        if (
            len(token)
            < self.MINIMUM_FUZZY_TERM_LENGTH
        ):
            return None

        maximum_distance = (
            2
            if len(token) >= 10
            else 1
        )

        minimum_candidate_length = (
            len(token)
            - maximum_distance
        )

        maximum_candidate_length = (
            len(token)
            + maximum_distance
        )

        for candidate_length in range(
            minimum_candidate_length,
            maximum_candidate_length + 1,
        ):
            candidates = (
                self._canonical_by_length.get(
                    candidate_length,
                    (),
                )
            )

            for candidate in candidates:
                if (
                    len(candidate)
                    < self.MINIMUM_FUZZY_TERM_LENGTH
                ):
                    continue

                if self._bounded_levenshtein(
                    token,
                    candidate,
                    maximum_distance=(
                        maximum_distance
                    ),
                ):
                    return "fuzzy"

        return None

    # ------------------------------------------------------

    @staticmethod
    def _normalize_lexicon_term(
        term: str,
    ) -> str:
        """
        Normalize one vocabulary entry.

        The vocabulary ultimately stores lowercase alphabetic
        tokens only.
        """

        normalized = unicodedata.normalize(
            "NFKC",
            term,
        ).lower()

        normalized = normalized.translate(
            LEET_TRANSLATION
        )

        return re.sub(
            r"[^a-z]",
            "",
            normalized,
        )

    # ------------------------------------------------------

    @staticmethod
    def _expand_term(
        term: str,
    ) -> set[str]:
        """
        Generate conservative common typo variants.

        Generated variants include:

        - Adjacent transposition:
          bullshit -> bullshti

        - Repeated internal character:
          bullshit -> bullshhit

        Deletion variants are intentionally not generated because
        they may collide with legitimate English words.
        """

        variants = {
            term,
        }

        if len(term) < 5:
            return variants

        # Adjacent-letter transpositions.
        for index in range(
            len(term) - 1
        ):
            if (
                term[index]
                == term[index + 1]
            ):
                continue

            transposed = (
                term[:index]
                + term[index + 1]
                + term[index]
                + term[index + 2:]
            )

            variants.add(
                transposed
            )

        # One repeated internal character.
        #
        # The first and final characters are excluded to reduce
        # false positives.
        for index in range(
            1,
            len(term) - 1,
        ):
            repeated = (
                term[:index]
                + term[index]
                + term[index:]
            )

            variants.add(
                repeated
            )

        return variants

    # ------------------------------------------------------

    @staticmethod
    def _bounded_levenshtein(
        left: str,
        right: str,
        *,
        maximum_distance: int,
    ) -> bool:
        """
        Return whether two strings are within a maximum
        Levenshtein edit distance.

        The function stops early when the distance cannot fall
        within the requested limit.

        Supported edit operations:

        - Character insertion
        - Character deletion
        - Character substitution
        """

        if left == right:
            return True

        if (
            abs(
                len(left)
                - len(right)
            )
            > maximum_distance
        ):
            return False

        if len(left) > len(right):
            left, right = (
                right,
                left,
            )

        previous_row = list(
            range(
                len(right) + 1
            )
        )

        for left_index, left_character in enumerate(
            left,
            start=1,
        ):
            current_row = [
                left_index,
            ]

            row_minimum = left_index

            for right_index, right_character in enumerate(
                right,
                start=1,
            ):
                insertion_cost = (
                    current_row[
                        right_index - 1
                    ]
                    + 1
                )

                deletion_cost = (
                    previous_row[
                        right_index
                    ]
                    + 1
                )

                substitution_cost = (
                    previous_row[
                        right_index - 1
                    ]
                    + (
                        left_character
                        != right_character
                    )
                )

                distance = min(
                    insertion_cost,
                    deletion_cost,
                    substitution_cost,
                )

                current_row.append(
                    distance
                )

                row_minimum = min(
                    row_minimum,
                    distance,
                )

            if (
                row_minimum
                > maximum_distance
            ):
                return False

            previous_row = current_row

        return (
            previous_row[-1]
            <= maximum_distance
        )


# ==========================================================
# Profanity Validator
# ==========================================================


class ProfanityValidator:
    """
    Detect profanity in recipient-facing communication.
    """

    checker_name: Final[str] = (
        "profanity_validator"
    )

    OUTPUT_FIELDS: Final[
        tuple[
            GuardrailField,
            ...,
        ]
    ] = (
        "title",
        "subject",
        "message",
    )

    PLACEHOLDER_PATTERN: Final[
        re.Pattern[str]
    ] = re.compile(
        r"\{\{[A-Za-z][A-Za-z0-9_]*\}\}"
    )

    TOKEN_PATTERN: Final[
        re.Pattern[str]
    ] = re.compile(
        r"[a-z]+"
    )

    REPEATED_CHARACTER_PATTERN: Final[
        re.Pattern[str]
    ] = re.compile(
        r"([a-z])\1{2,}"
    )

    # ------------------------------------------------------

    def __init__(
        self,
        lexicon: ProfanityLexicon | None = None,
    ) -> None:
        """
        Initialize the validator.

        A custom lexicon may be injected for testing or future
        tenant-specific configuration.
        """

        self._lexicon = (
            lexicon
            or ProfanityLexicon.default()
        )

    # ------------------------------------------------------

    def check(
        self,
        *,
        context: CommunicationContext,
        decision: CommunicationDecision,
    ) -> GuardrailCheckResult:
        """
        Scan generated title, subject, and message.

        The context parameter is accepted because every checker
        follows the common GuardrailChecker interface.

        The generated content is never copied into the returned
        violation.
        """

        from app.services.ai.FieldOpsAI.schemas.communication import output_text_for_validation
        
        started_at = perf_counter()

        violations: list[
            GuardrailViolation
        ] = []

        validation_text = output_text_for_validation(decision.output)

        if validation_text:
            lexicon_match_count = 0
            fuzzy_match_count = 0

            for token in self._tokenize(
                validation_text
            ):
                match_type = (
                    self._lexicon.classify(
                        token
                    )
                )

                if match_type == "lexicon":
                    lexicon_match_count += 1

                elif match_type == "fuzzy":
                    fuzzy_match_count += 1

            total_match_count = (
                lexicon_match_count
                + fuzzy_match_count
            )

            if total_match_count > 0:
                violations.append(
                    GuardrailViolation(
                        code="PROFANITY_DETECTED",
                        category=(
                            GuardrailCategory.PROFANITY
                        ),
                        severity=(
                            GuardrailSeverity.ERROR
                        ),
                        message=(
                            "Generated communication contains "
                            "prohibited language."
                        ),
                        field="output",
                        safe_metadata={
                            "match_count": (
                                total_match_count
                            ),
                            "lexicon_match_count": (
                                lexicon_match_count
                            ),
                            "fuzzy_match_count": (
                                fuzzy_match_count
                            ),
                        },
                    )
                )

        latency_ms = (
            perf_counter()
            - started_at
        ) * 1000

        return GuardrailCheckResult(
            checker_name=self.checker_name,
            passed=not violations,
            violations=tuple(
                violations
            ),
            latency_ms=latency_ms,
        )

    # ------------------------------------------------------

    @classmethod
    def _tokenize(
        cls,
        value: str,
    ) -> tuple[str, ...]:
        """
        Normalize recipient-facing content into safe tokens.

        Processing order:

        1. Remove valid placeholders from scanning
        2. Normalize Unicode
        3. Convert to lowercase
        4. Normalize common leetspeak
        5. Collapse three or more repeated letters to two
        6. Extract complete alphabetic tokens

        Placeholder masking prevents names such as
        CUSTOMER_NAME from being inspected as ordinary words.
        """

        without_placeholders = (
            cls.PLACEHOLDER_PATTERN.sub(
                " ",
                value,
            )
        )

        normalized = unicodedata.normalize(
            "NFKC",
            without_placeholders,
        ).lower()

        normalized = normalized.translate(
            LEET_TRANSLATION
        )

        normalized = (
            cls.REPEATED_CHARACTER_PATTERN.sub(
                r"\1\1",
                normalized,
            )
        )

        return tuple(
            cls.TOKEN_PATTERN.findall(
                normalized
            )
        )