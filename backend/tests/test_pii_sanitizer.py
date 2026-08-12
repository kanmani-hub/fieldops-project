"""
test_pii_sanitizer.py

Unit tests for the FieldOps PII sanitization system.

These tests verify:

- Structured PII replacement
- Nested-object sanitization
- Free-text regex sanitization
- Placeholder formatting
- Accurate restoration
- Request-scoped mapping isolation
- Prompt leakage validation
- Mapping cleanup after restoration
"""

from __future__ import annotations

import json,re,pytest

from pydantic import BaseModel

from app.services.ai.pii_sanitizer import (
    ContextualNameRecognizer,
    PIICategory,
    PIILeakageError,
    PIISanitizer,
    PlaceholderMap,
)


# ==========================================================
# Test Models
# ==========================================================


class TestCustomerModel(BaseModel):
    """
    Example nested customer model.
    """

    name: str
    phone: str
    email: str


class TestRequestModel(BaseModel):
    """
    Example Pydantic request containing PII.
    """

    job_id: str
    customer: TestCustomerModel


# Prevent pytest from treating these Pydantic models as tests.
TestCustomerModel.__test__ = False
TestRequestModel.__test__ = False


# ==========================================================
# Fixtures
# ==========================================================


@pytest.fixture
def sanitizer() -> PIISanitizer:
    """
    Return a fresh stateless sanitizer.
    """

    return PIISanitizer()


@pytest.fixture
def complete_pii_payload() -> dict[str, object]:
    """
    Return a representative FieldOps payload containing
    all required PII categories.
    """

    return {
        "job_id": "JOB-1001",
        "customer": {
            "name": "Ruby Devi",
            "phone": "+91 9876543210",
            "email": "ruby@example.com",
            "address": {
                "street": "123 Main Street",
                "city": "Chennai",
                "state": "Tamil Nadu",
                "postal_code": "600001",
            },
        },
        "service_address": "456 Service Road",
        "technician": {
            "name": "Kumar Raj",
            "phone": "+91 9988776655",
            "location": {
                "latitude": 13.0827,
                "longitude": 80.2707,
            },
        },
    }


# ==========================================================
# Structured PII Tests
# ==========================================================


def test_sanitize_replaces_all_required_structured_pii(
    sanitizer: PIISanitizer,
    complete_pii_payload: dict[str, object],
) -> None:
    """
    All known structured PII fields must become placeholders.
    """

    result = sanitizer.sanitize(
        complete_pii_payload
    )

    sanitized = result.sanitized_data

    assert sanitized["job_id"] == "{{job_id}}"

    assert (
        sanitized["customer"]["name"]
        == "{{customer_name}}"
    )

    assert (
        sanitized["customer"]["phone"]
        == "{{customer_phone}}"
    )

    assert (
        sanitized["customer"]["email"]
        == "{{customer_email}}"
    )

    assert (
        sanitized["service_address"]
        == "{{service_address}}"
    )

    assert (
        sanitized["technician"]["name"]
        == "{{technician_name}}"
    )

    assert (
        sanitized["technician"]["phone"]
        == "{{technician_phone}}"
    )

    assert result.replacement_count >= 6

    assert (
        result.replacement_count
        == len(result.placeholder_map)
    )


def test_nested_customer_address_fields_are_sanitized(
    sanitizer: PIISanitizer,
    complete_pii_payload: dict[str, object],
) -> None:
    """
    Nested address fields must use meaningful placeholders.
    """

    result = sanitizer.sanitize(
        complete_pii_payload
    )

    address = result.sanitized_data[
        "customer"
    ]["address"]

    assert (
        address["street"]
        == "{{customer_street}}"
    )

    assert (
        address["city"]
        == "{{customer_city}}"
    )

    assert (
        address["state"]
        == "{{customer_state}}"
    )

    assert (
        address["postal_code"]
        == "{{customer_postal_code}}"
    )


def test_nested_technician_location_is_sanitized(
    sanitizer: PIISanitizer,
    complete_pii_payload: dict[str, object],
) -> None:
    """
    Nested latitude and longitude values must be replaced.
    """

    result = sanitizer.sanitize(
        complete_pii_payload
    )

    location = result.sanitized_data[
        "technician"
    ]["location"]

    assert (
        location["latitude"]
        == "{{technician_latitude}}"
    )

    assert (
        location["longitude"]
        == "{{technician_longitude}}"
    )

    assert (
        result.placeholder_map.values[
            "{{technician_latitude}}"
        ]
        == 13.0827
    )

    assert (
        result.placeholder_map.values[
            "{{technician_longitude}}"
        ]
        == 80.2707
    )


def test_pydantic_models_are_supported(
    sanitizer: PIISanitizer,
) -> None:
    """
    Pydantic models must be converted and sanitized.
    """

    request = TestRequestModel(
        job_id="JOB-2002",
        customer=TestCustomerModel(
            name="Priya Sharma",
            phone="+91 9123456789",
            email="priya@example.com",
        ),
    )

    result = sanitizer.sanitize(
        request
    )

    assert (
        result.sanitized_data["job_id"]
        == "{{job_id}}"
    )

    assert (
        result.sanitized_data[
            "customer"
        ]["name"]
        == "{{customer_name}}"
    )

    assert (
        result.sanitized_data[
            "customer"
        ]["phone"]
        == "{{customer_phone}}"
    )

    assert (
        result.sanitized_data[
            "customer"
        ]["email"]
        == "{{customer_email}}"
    )


# ==========================================================
# Free-Text Sanitization Tests
# ==========================================================


def test_free_text_pii_is_replaced_using_regex(
    sanitizer: PIISanitizer,
) -> None:
    """
    High-confidence PII inside normal text must be replaced.
    """

    original_text = (
        "Contact ruby@example.com or +91 9876543210. "
        "The service location is 123 Main Street. "
        "The work order is JOB-1001. "
        "Technician coordinates are 13.0827, 80.2707. "
        "The SSN is 123-45-6789."
    )

    result = sanitizer.sanitize(
        original_text
    )

    sanitized = result.sanitized_data

    assert "ruby@example.com" not in sanitized
    assert "+91 9876543210" not in sanitized
    assert "123 Main Street" not in sanitized
    assert "JOB-1001" not in sanitized
    assert "13.0827, 80.2707" not in sanitized
    assert "123-45-6789" not in sanitized

    assert "{{detected_email}}" in sanitized
    assert "{{detected_phone}}" in sanitized
    assert "{{detected_address}}" in sanitized
    assert "{{detected_job_id}}" in sanitized
    assert "{{detected_location}}" in sanitized
    assert "{{detected_ssn}}" in sanitized
    assert "The work order is " in sanitized
    assert (sanitized.count("{{detected_job_id}}")== 1)


def test_sanitize_prompt_uses_existing_placeholder_map(
    sanitizer: PIISanitizer,
) -> None:
    """
    Prompt sanitization must be able to add values to an
    existing request-scoped placeholder map.
    """

    structured_result = sanitizer.sanitize(
        {
            "customer_name": "Ruby Devi",
        }
    )

    prompt = (
        "Send an update to {{customer_name}} at "
        "ruby@example.com."
    )

    sanitized_prompt, placeholder_map = (
        sanitizer.sanitize_prompt(
            prompt,
            placeholder_map=(
                structured_result.placeholder_map
            ),
        )
    )

    assert "{{customer_name}}" in sanitized_prompt
    assert "{{detected_email}}" in sanitized_prompt

    assert (
        placeholder_map.values[
            "{{customer_name}}"
        ]
        == "Ruby Devi"
    )

    assert (
        placeholder_map.values[
            "{{detected_email}}"
        ]
        == "ruby@example.com"
    )


# ==========================================================
# Restoration Tests
# ==========================================================


def test_restore_data_restores_original_structure_exactly(
    sanitizer: PIISanitizer,
    complete_pii_payload: dict[str, object],
) -> None:
    """
    Sanitization followed by restoration must return the
    original nested data accurately.
    """

    result = sanitizer.sanitize(
        complete_pii_payload
    )

    restored = sanitizer.restore_data(
        result.sanitized_data,
        result.placeholder_map,
    )

    assert restored == complete_pii_payload


def test_placeholder_mapping_is_cleared_after_restoration(
    sanitizer: PIISanitizer,
) -> None:
    """
    Sensitive mappings must be removed after restoration.
    """

    result = sanitizer.sanitize(
        {
            "customer_name": "Ruby Devi",
            "customer_phone": "+91 9876543210",
        }
    )

    assert len(result.placeholder_map) == 2

    restored = sanitizer.restore_data(
        result.sanitized_data,
        result.placeholder_map,
    )

    assert restored == {
        "customer_name": "Ruby Devi",
        "customer_phone": "+91 9876543210",
    }

    assert len(result.placeholder_map) == 0
    assert result.placeholder_map.values == {}
    assert result.placeholder_map.categories == {}


def test_mapping_can_be_retained_when_explicitly_requested(
    sanitizer: PIISanitizer,
) -> None:
    """
    Internal workflows may temporarily retain the map when
    clear_mapping=False is explicitly supplied.
    """

    result = sanitizer.sanitize(
        {
            "job_id": "JOB-3003",
        }
    )

    restored = sanitizer.restore_data(
        result.sanitized_data,
        result.placeholder_map,
        clear_mapping=False,
    )

    assert restored["job_id"] == "JOB-3003"

    assert (
        result.placeholder_map.values[
            "{{job_id}}"
        ]
        == "JOB-3003"
    )


def test_tuple_structure_is_preserved_during_restore(
    sanitizer: PIISanitizer,
) -> None:
    """
    Tuple values should remain tuples after round-trip.
    """

    original = {
        "contacts": (
            "ruby@example.com",
            "+91 9876543210",
        )
    }

    result = sanitizer.sanitize(
        original
    )

    restored = sanitizer.restore_data(
        result.sanitized_data,
        result.placeholder_map,
    )

    assert restored == original
    assert isinstance(
        restored["contacts"],
        tuple,
    )


# ==========================================================
# Placeholder Map Tests
# ==========================================================


def test_same_value_reuses_existing_placeholder() -> None:
    """
    Repeated values in the same category should not create
    duplicate mappings.
    """

    placeholder_map = PlaceholderMap()

    first = placeholder_map.add(
        preferred_name="customer_name",
        category=PIICategory.CUSTOMER_NAME,
        value="Ruby Devi",
    )

    second = placeholder_map.add(
        preferred_name="client_name",
        category=PIICategory.CUSTOMER_NAME,
        value="Ruby Devi",
    )

    assert first == "{{customer_name}}"
    assert second == "{{customer_name}}"
    assert len(placeholder_map) == 1


def test_different_values_receive_unique_placeholder_suffixes() -> None:
    """
    Two different values requesting the same placeholder name
    must receive unique placeholders.
    """

    placeholder_map = PlaceholderMap()

    first = placeholder_map.add(
        preferred_name="customer_name",
        category=PIICategory.CUSTOMER_NAME,
        value="Ruby Devi",
    )

    second = placeholder_map.add(
        preferred_name="customer_name",
        category=PIICategory.CUSTOMER_NAME,
        value="Priya Sharma",
    )

    assert first == "{{customer_name}}"
    assert second == "{{customer_name_2}}"

    assert (
        placeholder_map.values[first]
        == "Ruby Devi"
    )

    assert (
        placeholder_map.values[second]
        == "Priya Sharma"
    )


# ==========================================================
# Leakage Validation Tests
# ==========================================================


def test_validation_accepts_safe_placeholders(
    sanitizer: PIISanitizer,
) -> None:
    """
    Placeholder tokens must not be mistaken for real PII.
    """

    safe_prompt = (
        "Customer: {{customer_name}}\n"
        "Phone: {{customer_phone}}\n"
        "Email: {{customer_email}}\n"
        "Address: {{service_address}}\n"
        "Job: {{job_id}}\n"
        "Location: {{technician_location}}"
    )

    sanitizer.validate_no_pii(
        safe_prompt
    )


@pytest.mark.parametrize(
    (
        "unsafe_text",
        "expected_category",
    ),
    [
        (
            "Email the customer at ruby@example.com.",
            PIICategory.EMAIL,
        ),
        (
            "Call the customer on +91 9876543210.",
            PIICategory.PHONE,
        ),
        (
            "The SSN is 123-45-6789.",
            PIICategory.SSN,
        ),
        (
            "Visit 123 Main Street.",
            PIICategory.ADDRESS,
        ),
        (
            "Job reference is JOB-1001.",
            PIICategory.JOB_ID,
        ),
        (
            "Current GPS is 13.0827, 80.2707.",
            PIICategory.LOCATION,
        ),
    ],
)
def test_validation_detects_remaining_pii(
    sanitizer: PIISanitizer,
    unsafe_text: str,
    expected_category: PIICategory,
) -> None:
    """
    Validation must reject prompts containing raw PII.
    """

    with pytest.raises(
        PIILeakageError
    ) as exc_info:
        sanitizer.validate_no_pii(
            unsafe_text
        )

    assert (
        expected_category
        in exc_info.value.categories
    )


def test_leakage_error_does_not_expose_sensitive_value(
    sanitizer: PIISanitizer,
) -> None:
    """
    Error messages must identify the category without copying
    the actual private value into logs.
    """

    private_email = "secret.customer@example.com"

    with pytest.raises(
        PIILeakageError
    ) as exc_info:
        sanitizer.validate_no_pii(
            f"Contact {private_email}"
        )

    error_text = str(
        exc_info.value
    )

    assert private_email not in error_text
    assert "email" in error_text


# ==========================================================
# Privacy and Request-Isolation Tests
# ==========================================================


def test_no_original_pii_appears_in_sanitized_json(
    sanitizer: PIISanitizer,
    complete_pii_payload: dict[str, object],
) -> None:
    """
    Serialized sanitized context must contain no original PII.
    """

    result = sanitizer.sanitize(
        complete_pii_payload
    )

    serialized = json.dumps(
        result.sanitized_data,
        ensure_ascii=False,
        default=str,
    )

    private_values = (
        "JOB-1001",
        "Ruby Devi",
        "+91 9876543210",
        "ruby@example.com",
        "123 Main Street",
        "Chennai",
        "Tamil Nadu",
        "600001",
        "456 Service Road",
        "Kumar Raj",
        "+91 9988776655",
        "13.0827",
        "80.2707",
    )

    for private_value in private_values:
        assert private_value not in serialized


def test_each_request_receives_an_independent_mapping(
    sanitizer: PIISanitizer,
) -> None:
    """
    One request's PII must never appear in another request's
    placeholder mapping.
    """

    first_result = sanitizer.sanitize(
        {
            "customer_name": "Ruby Devi",
        }
    )

    second_result = sanitizer.sanitize(
        {
            "customer_name": "Priya Sharma",
        }
    )

    assert (
        first_result.placeholder_map
        is not second_result.placeholder_map
    )

    assert (
        first_result.placeholder_map.values[
            "{{customer_name}}"
        ]
        == "Ruby Devi"
    )

    assert (
        second_result.placeholder_map.values[
            "{{customer_name}}"
        ]
        == "Priya Sharma"
    )

    assert (
        "Priya Sharma"
        not in first_result.placeholder_map.values.values()
    )

    assert (
        "Ruby Devi"
        not in second_result.placeholder_map.values.values()
    )


# ==========================================================
# Invalid Input Tests
# ==========================================================


def test_sanitize_prompt_rejects_non_string_input(
    sanitizer: PIISanitizer,
) -> None:
    """
    Prompts must always be strings.
    """

    with pytest.raises(
        TypeError,
        match="Prompt must be a string",
    ):
        sanitizer.sanitize_prompt(
            123,  # type: ignore[arg-type]
        )


def test_validate_no_pii_rejects_non_string_input(
    sanitizer: PIISanitizer,
) -> None:
    """
    Prompt validation must reject unsupported input types.
    """

    with pytest.raises(
        TypeError,
        match="Text must be a string",
    ):
        sanitizer.validate_no_pii(
            {"unsafe": "value"}  # type: ignore[arg-type]
        )


def test_restore_rejects_invalid_placeholder_map(
    sanitizer: PIISanitizer,
) -> None:
    """
    Restoration must require the correct map type.
    """

    with pytest.raises(
        TypeError,
        match=(
            "placeholder_map must be "
            "a PlaceholderMap"
        ),
    ):
        sanitizer.restore_data(
            data="{{customer_name}}",
            placeholder_map={},  # type: ignore[arg-type]
        )

# ==========================================================
# Local Named-Entity Recognition Tests
# ==========================================================


def test_contextual_names_in_free_text_are_sanitized(
    sanitizer: PIISanitizer,
) -> None:
    """
    Customer and technician names appearing only in free text
    must be replaced by the local NER fallback.
    """

    original = (
        "Customer Ruby Devi requested an update. "
        "Technician Kumar Raj accepted the job."
    )

    result = sanitizer.sanitize(
        original
    )

    sanitized = result.sanitized_data

    assert "Ruby Devi" not in sanitized
    assert "Kumar Raj" not in sanitized

    assert "{{customer_name}}" in sanitized
    assert "{{technician_name}}" in sanitized

    assert (
        result.placeholder_map.values[
            "{{customer_name}}"
        ]
        == "Ruby Devi"
    )

    assert (
        result.placeholder_map.values[
            "{{technician_name}}"
        ]
        == "Kumar Raj"
    )


def test_contextual_names_are_restored_accurately(
    sanitizer: PIISanitizer,
) -> None:
    """
    Locally recognized names must restore exactly.
    """

    original = (
        "Customer Ruby Devi requested service from "
        "technician Kumar Raj."
    )

    result = sanitizer.sanitize(
        original
    )

    restored = sanitizer.restore_data(
        result.sanitized_data,
        result.placeholder_map,
    )

    assert restored == original

    assert len(
        result.placeholder_map
    ) == 0


def test_known_structured_name_is_reused_in_free_text(
    sanitizer: PIISanitizer,
) -> None:
    """
    A name stored in a structured field and repeated in prose
    must reuse one placeholder mapping.
    """

    result = sanitizer.sanitize(
        {
            "customer_name": "Ruby Devi",
            "additional_context": (
                "Ruby Devi requested an evening visit."
            ),
        }
    )

    sanitized = result.sanitized_data

    assert (
        sanitized["customer_name"]
        == "{{customer_name}}"
    )

    assert (
        sanitized["additional_context"]
        == (
            "{{customer_name}} requested "
            "an evening visit."
        )
    )

    customer_placeholders = [
        placeholder
        for (
            placeholder,
            category,
        ) in result.placeholder_map.categories.items()
        if (
            category
            == PIICategory.CUSTOMER_NAME
        )
    ]

    assert customer_placeholders == [
        "{{customer_name}}"
    ]


def test_validation_blocks_contextual_person_names(
    sanitizer: PIISanitizer,
) -> None:
    """
    Final prompt validation must block contextual names that
    were not sanitized.
    """

    unsafe_prompt = (
        "Customer Ruby Devi requested an update."
    )

    with pytest.raises(
        PIILeakageError
    ) as exc_info:
        sanitizer.validate_no_pii(
            unsafe_prompt
        )

    assert (
        PIICategory.CUSTOMER_NAME
        in exc_info.value.categories
    )


def test_normal_service_phrase_is_not_treated_as_name(
    sanitizer: PIISanitizer,
) -> None:
    """
    Common FieldOps phrases should not create false person-name
    entities.
    """

    safe_text = (
        "Customer Service Team will review the request."
    )

    result = sanitizer.sanitize(
        safe_text
    )

    assert result.sanitized_data == safe_text
    assert result.replacement_count == 0

# ==========================================================
# Coverage and Defensive-Branch Tests
# ==========================================================


def test_placeholder_map_recovers_existing_value_when_reverse_index_missing() -> None:
    """
    If the runtime reverse index is temporarily empty but the
    placeholder already contains the same value, the existing
    placeholder must be reused.
    """

    placeholder_map = PlaceholderMap()

    first_placeholder = placeholder_map.add(
        preferred_name="customer_name",
        category=PIICategory.CUSTOMER_NAME,
        value="Ruby Devi",
    )

    # Simulate reconstruction of the internal lookup index.
    # The public placeholder data still exists.
    placeholder_map._reverse_index.clear()

    second_placeholder = placeholder_map.add(
        preferred_name="customer_name",
        category=PIICategory.CUSTOMER_NAME,
        value="Ruby Devi",
    )

    assert first_placeholder == "{{customer_name}}"
    assert second_placeholder == "{{customer_name}}"

    assert len(placeholder_map) == 1

    assert (
        placeholder_map._reverse_index[
            (
                PIICategory.CUSTOMER_NAME.value,
                '"Ruby Devi"',
            )
        ]
        == "{{customer_name}}"
    )


def test_placeholder_name_normalization_handles_empty_and_numeric_names() -> None:
    """
    Invalid preferred names must be converted into valid
    placeholder identifiers.
    """

    placeholder_map = PlaceholderMap()

    empty_name_placeholder = placeholder_map.add(
        preferred_name="!!!",
        category=PIICategory.NESTED_FIELD,
        value="first value",
    )

    numeric_name_placeholder = placeholder_map.add(
        preferred_name="123 customer",
        category=PIICategory.NESTED_FIELD,
        value="second value",
    )

    assert (
        empty_name_placeholder
        == "{{pii_value}}"
    )

    assert (
        numeric_name_placeholder
        == "{{value_123_customer}}"
    )


def test_placeholder_fingerprint_handles_circular_values() -> None:
    """
    Circular Python values cannot be JSON serialized normally.

    The placeholder map must fall back to a safe in-memory
    representation instead of crashing.
    """

    circular_value: list[object] = []

    circular_value.append(
        circular_value
    )

    placeholder_map = PlaceholderMap()

    placeholder = placeholder_map.add(
        preferred_name="circular_value",
        category=PIICategory.NESTED_FIELD,
        value=circular_value,
    )

    assert placeholder == "{{circular_value}}"

    assert placeholder in placeholder_map.values


def test_contextual_name_recognizer_rejects_non_string_input() -> None:
    """
    The local NER component must reject unsupported input.
    """

    recognizer = ContextualNameRecognizer()

    with pytest.raises(
        TypeError,
        match="NER input must be a string",
    ):
        recognizer.detect(
            123,  # type: ignore[arg-type]
        )


def test_contextual_name_recognizer_skips_duplicate_spans() -> None:
    """
    When two recognition rules return the same text span,
    only one entity must be returned.
    """

    recognizer = ContextualNameRecognizer()

    duplicate_pattern = re.compile(
        r"(?P<name>Ruby Devi)"
    )

    recognizer.CUSTOMER_PATTERN = (
        duplicate_pattern
    )

    recognizer.TECHNICIAN_PATTERN = (
        duplicate_pattern
    )

    recognizer.TITLED_PERSON_PATTERN = (
        re.compile(
            r"(?!)"
        )
    )

    entities = recognizer.detect(
        "Ruby Devi"
    )

    assert len(entities) == 1

    assert entities[0].text == "Ruby Devi"


def test_contextual_name_recognizer_skips_overlapping_spans() -> None:
    """
    Partially overlapping entities must not both be returned.
    """

    recognizer = ContextualNameRecognizer()

    recognizer.CUSTOMER_PATTERN = re.compile(
        r"(?P<name>Ruby Devi)"
    )

    recognizer.TECHNICIAN_PATTERN = re.compile(
        r"(?P<name>Devi Raj)"
    )

    recognizer.TITLED_PERSON_PATTERN = re.compile(
        r"(?!)"
    )

    entities = recognizer.detect(
        "Ruby Devi Raj"
    )

    assert len(entities) == 1

    assert entities[0].text == "Ruby Devi"


def test_restore_alias_handles_lists_and_non_string_values(
    sanitizer: PIISanitizer,
) -> None:
    """
    The restore() alias must support list values and preserve
    non-string values accurately.
    """

    original = [
        "ruby@example.com",
        42,
        True,
        None,
    ]

    result = sanitizer.sanitize(
        original
    )

    assert isinstance(
        result.sanitized_data,
        list,
    )

    assert (
        result.sanitized_data[0]
        == "{{detected_email}}"
    )

    assert result.sanitized_data[1] == 42
    assert result.sanitized_data[2] is True
    assert result.sanitized_data[3] is None

    restored = sanitizer.restore(
        result.sanitized_data,
        result.placeholder_map,
    )

    assert restored == original

    assert len(
        result.placeholder_map
    ) == 0


def test_known_value_replacement_ignores_unsafe_candidates(
    sanitizer: PIISanitizer,
) -> None:
    """
    Known-value replacement must ignore:

    - Non-string original values
    - Empty strings
    - Very short nested-field values
    """

    placeholder_map = PlaceholderMap()

    placeholder_map.add(
        preferred_name="technician_latitude",
        category=PIICategory.LOCATION,
        value=13.0827,
    )

    placeholder_map.add(
        preferred_name="customer_name",
        category=PIICategory.CUSTOMER_NAME,
        value="",
    )

    placeholder_map.add(
        preferred_name="customer_city",
        category=PIICategory.NESTED_FIELD,
        value="LA",
    )

    original_text = (
        "This text does not contain a reusable "
        "private value."
    )

    sanitized = sanitizer._replace_known_values(
        text=original_text,
        placeholder_map=placeholder_map,
    )

    assert sanitized == original_text


def test_nested_job_id_field_is_sanitized(
    sanitizer: PIISanitizer,
) -> None:
    """
    A nested job.id field must be recognized as a job ID.
    """

    result = sanitizer.sanitize(
        {
            "job": {
                "id": "JOB-9001",
                "status": "ASSIGNED",
            },
        }
    )

    assert (
        result.sanitized_data[
            "job"
        ]["id"]
        == "{{job_id}}"
    )

    assert (
        result.sanitized_data[
            "job"
        ]["status"]
        == "ASSIGNED"
    )

    assert (
        result.placeholder_map.values[
            "{{job_id}}"
        ]
        == "JOB-9001"
    )