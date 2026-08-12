import pytest

from app.services.ai.FieldOpsAI.services.prompt_locale_service import (
    normalize_locale,
    locale_candidates,
    InvalidLocaleError,
)

def test_normalize_locale():
    assert normalize_locale("en") == "en"
    assert normalize_locale("es-mx") == "es-MX"
    assert normalize_locale("es-MX") == "es-MX"
    assert normalize_locale("es_MX") == "es-MX"
    assert normalize_locale("ta") == "ta"

    with pytest.raises(InvalidLocaleError):
        normalize_locale("")
    
    with pytest.raises(InvalidLocaleError):
        normalize_locale(" ")

    with pytest.raises(InvalidLocaleError):
        normalize_locale("english")
        
    with pytest.raises(InvalidLocaleError):
        normalize_locale("fr")  # Unsupported language

def test_locale_candidates():
    assert locale_candidates("es-MX") == ("es-MX", "es", "en")
    assert locale_candidates("es") == ("es", "en")
    assert locale_candidates("en-US") == ("en-US", "en")
    assert locale_candidates("en") == ("en",)


from app.models import NotificationTemplate
from app.services.ai.FieldOpsAI.services.prompt_locale_service import validate_translation_parity

def test_validate_translation_parity_success():
    en_t = NotificationTemplate(
        locale="en",
        variables=[{"name": "foo", "required": True}],
        body_template="Hello {{foo}}",
        title_template=None
    )
    es_t = NotificationTemplate(
        locale="es",
        variables=[{"name": "foo", "required": True}],
        body_template="Hola {{foo}}",
        title_template=None
    )
    issues = validate_translation_parity(en_t, es_t)
    assert not issues

def test_validate_translation_parity_missing_var():
    en_t = NotificationTemplate(
        locale="en",
        variables=[{"name": "foo", "required": True}],
        body_template="Hello {{foo}}",
        title_template=None
    )
    es_t = NotificationTemplate(
        locale="es",
        variables=[],
        body_template="Hola",
        title_template=None
    )
    issues = validate_translation_parity(en_t, es_t)
    assert len(issues) > 0
    assert any(i.issue_code == "VARIABLE_PATH_MISMATCH" for i in issues)

def test_validate_translation_parity_extra_var():
    en_t = NotificationTemplate(
        locale="en",
        variables=[],
        body_template="Hello",
        title_template=None
    )
    es_t = NotificationTemplate(
        locale="es",
        variables=[{"name": "foo", "required": True}],
        body_template="Hola {{foo}}",
        title_template=None
    )
    issues = validate_translation_parity(en_t, es_t)
    assert len(issues) > 0
    assert any(i.issue_code == "VARIABLE_PATH_MISMATCH" for i in issues)

def test_validate_translation_parity_required_mismatch():
    en_t = NotificationTemplate(
        locale="en",
        variables=[{"name": "foo", "required": True}],
        body_template="Hello {{foo}}",
        title_template=None
    )
    es_t = NotificationTemplate(
        locale="es",
        variables=[{"name": "foo", "required": False}],
        body_template="Hola {{foo}}",
        title_template=None
    )
    issues = validate_translation_parity(en_t, es_t)
    assert len(issues) > 0
    assert any(i.issue_code == "REQUIRED_FLAG_MISMATCH" for i in issues)



from app.services.ai.FieldOpsAI.services.prompt_locale_service import _get_shape

def test_recursive_default_shape_match():
    en_t = NotificationTemplate(
        locale="en",
        variables=[
            {
                "name": "foo",
                "required": False,
                "default": {
                    "a": 1,
                    "b": [1, 2],
                },
            }
        ],
        body_template="{{ foo }}",
        title_template=None,
    )

    es_t = NotificationTemplate(
        locale="es",
        variables=[
            {
                "name": "foo",
                "required": False,
                "default": {
                    "a": 2,
                    "b": [3, 4],
                },
            }
        ],
        body_template="{{ foo }}",
        title_template=None,
    )

    issues = validate_translation_parity(
        en_t,
        es_t,
    )

    assert not issues

def test_recursive_default_shape_mismatch():
    en_t = NotificationTemplate(
        locale="en",
        variables=[
            {
                "name": "foo",
                "required": False,
                "default": {
                    "a": 1,
                },
            }
        ],
        body_template="{{ foo }}",
        title_template=None,
    )

    es_t = NotificationTemplate(
        locale="es",
        variables=[
            {
                "name": "foo",
                "required": False,
                "default": [1],
            }
        ],
        body_template="{{ foo }}",
        title_template=None,
    )

    issues = validate_translation_parity(
        en_t,
        es_t,
    )

    assert any(
        issue.issue_code
        == "DEFAULT_TYPE_MISMATCH"
        for issue in issues
    )
def test_invalid_canonical_english_is_reported():
    en_t = NotificationTemplate(locale="en", variables=[], body_template="Hello {{ bad }", title_template=None)
    es_t = NotificationTemplate(locale="es", variables=[], body_template="Hola", title_template=None)
    issues = validate_translation_parity(en_t, es_t)
    assert any(i.issue_code == "TEMPLATE_VALIDATION_FAILED" for i in issues)
