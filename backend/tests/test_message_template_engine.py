import pytest
from unittest.mock import Mock, patch
from fastapi import HTTPException
import threading


from app.services.template_engine import (
    render_managed_template,
    render_template_source,
    render_preview,
    render_notification,
    infer_template_declarations,
    MessageTemplateEngineError,
    MessageTemplateLookupError,
    MessageTemplateRenderingError,
    UnsupportedTemplateFormatError,
    _shared_injector
)
from app.services.ai.FieldOpsAI.services.prompt_variable_injector import (
    PromptVariableInjectionError,
    InvalidTemplateSyntaxError,
    MissingRequiredVariableError
)
from app.services.ai.FieldOpsAI.schemas.prompt_variable import PromptVariableDefinition, PromptVariableDeclaration
from app.services.ai.FieldOpsAI.schemas.prompt_template import PromptTemplateLookupResponse
from app.services.ai.guardrails.fallback_service import GuardrailFallbackService

# ---------------------------------------------------------
# 1. Rendering Tests
# ---------------------------------------------------------

def test_text_rendering():
    res = render_template_source(
        body="Hello {{ name }}",
        variables=[PromptVariableDefinition(name="name")],
        context={"name": "Alice"},
        format="text"
    )
    assert res.body == "Hello Alice"

def test_html_rendering():
    res = render_template_source(
        body="<b>Hello</b> {{ name }}",
        variables=[PromptVariableDefinition(name="name")],
        context={"name": "Alice"},
        format="html"
    )
    assert res.body == "<b>Hello</b> Alice"

def test_html_context_escaping():
    res = render_template_source(
        body="Hello {{ name }}",
        variables=[PromptVariableDefinition(name="name")],
        context={"name": "<script>alert(1)</script>"},
        format="html"
    )
    assert res.body == "Hello &lt;script&gt;alert(1)&lt;/script&gt;"

def test_static_html_preservation():
    res = render_template_source(
        body="<div><p>Static</p>{{ name }}</div>",
        variables=[PromptVariableDefinition(name="name")],
        context={"name": "Val"},
        format="html"
    )
    assert res.body == "<div><p>Static</p>Val</div>"

def test_title_rendering():
    res = render_template_source(
        body="Body",
        title="Title {{ name }}",
        variables=[PromptVariableDefinition(name="name")],
        context={"name": "Alice"},
        format="text"
    )
    assert res.title == "Title Alice"
    assert res.body == "Body"

def test_title_uses_text_mode():
    res = render_template_source(
        body="Body",
        title="Title {{ name }}",
        variables=[PromptVariableDefinition(name="name")],
        context={"name": "<b>Bold</b>"},
        format="html" # Format applies to body, title is always text and NOT escaped! Wait, is title escaped?
        # The code for injector does not use html_env for title! It explicitly passes html=False for title!
    )
    assert res.title == "Title <b>Bold</b>" # No escaping

def test_nested_paths():
    res = render_template_source(
        body="{{ user.address.city }}",
        variables=[PromptVariableDefinition(name="user.address.city")],
        context={"user": {"address": {"city": "Paris"}}},
        format="text"
    )
    assert res.body == "Paris"

def test_optional_defaults():
    res = render_template_source(
        body="Hello {{ name }}",
        variables=[PromptVariableDefinition(name="name", required=False, default="Guest")],
        context={},
        format="text"
    )
    assert res.body == "Hello Guest"

def test_required_variable_failure():
    with pytest.raises(MessageTemplateRenderingError) as exc:
        render_template_source(
            body="Hello {{ name }}",
            variables=[PromptVariableDefinition(name="name", required=True)],
            context={},
            format="text"
        )
    assert "Template rendering failed." in str(exc.value)

def test_caller_context_unchanged():
    ctx = {"name": "Alice"}
    render_template_source(
        body="Hello {{ name }} {{ other }}",
        variables=[PromptVariableDefinition(name="name"), PromptVariableDefinition(name="other", required=False, default="def")],
        context=ctx,
        format="text"
    )
    assert ctx == {"name": "Alice"} # unchanged

# ---------------------------------------------------------
# 2. Caching Behavior Tests
# ---------------------------------------------------------

def test_cache_miss_and_hit():
    _shared_injector._clear_cache()
    vars = [PromptVariableDefinition(name="name")]
    ctx = {"name": "Alice"}
    
    # First call: Miss + Compile
    res1 = render_template_source(body="Hello {{ name }}", variables=vars, context=ctx)
    info1 = _shared_injector.compiled_cache_info()
    assert info1["current_size"] == 1
    assert info1["misses"] == 1
    assert info1["hits"] == 0
    
    # Second call: Hit
    res2 = render_template_source(body="Hello {{ name }}", variables=vars, context=ctx)
    info2 = _shared_injector.compiled_cache_info()
    assert info2["current_size"] == 1
    assert info2["misses"] == 1
    assert info2["hits"] == 1

def test_html_and_text_use_separate_entries():
    _shared_injector._clear_cache()
    source = "Data {{ val }}"
    vars = [PromptVariableDefinition(name="val")]
    ctx = {"val": "1"}
    
    _shared_injector.render(body=source, variables=vars, context=ctx, html=False)
    _shared_injector.render(body=source, variables=vars, context=ctx, html=True)
    
    info = _shared_injector.compiled_cache_info()
    assert info["current_size"] == 2
    assert info["misses"] == 2

def test_title_and_body_compile_independently():
    _shared_injector._clear_cache()
    vars = [PromptVariableDefinition(name="val")]
    ctx = {"val": "1"}
    
    _shared_injector.render(body="Body {{ val }}", title="Title {{ val }}", variables=vars, context=ctx)
    info = _shared_injector.compiled_cache_info()
    assert info["current_size"] == 2

def test_invalid_syntax_is_not_cached():
    _shared_injector._clear_cache()
    from app.services.ai.FieldOpsAI.services.prompt_variable_injector import InvalidTemplateSyntaxError
    with pytest.raises(InvalidTemplateSyntaxError):
        _shared_injector.render(body="Hello {% if %}", variables=[], context={})
    info = _shared_injector.compiled_cache_info()
    assert info["current_size"] == 0

def test_unsafe_templates_are_not_cached():
    _shared_injector._clear_cache()
    from app.services.ai.FieldOpsAI.services.prompt_variable_injector import PromptVariableInjectionError
    with pytest.raises(PromptVariableInjectionError):
        _shared_injector.render(body="Hello {{ val.__class__ }}", variables=[PromptVariableDefinition(name="val")], context={"val": "1"})
    info = _shared_injector.compiled_cache_info()
    assert info["current_size"] == 0

def test_missing_runtime_data_does_not_remove_safe_entry():
    _shared_injector._clear_cache()
    from app.services.ai.FieldOpsAI.services.prompt_variable_injector import MissingRequiredVariableError
    # First we render successfully so it is cached
    _shared_injector.render(body="Hello {{ val }}", variables=[PromptVariableDefinition(name="val", required=True)], context={"val": "1"})
    info = _shared_injector.compiled_cache_info()
    assert info["current_size"] == 1
    # Now it is in cache, missing data should not remove it
    with pytest.raises(MissingRequiredVariableError):
        _shared_injector.render(body="Hello {{ val }}", variables=[PromptVariableDefinition(name="val", required=True)], context={})
    info2 = _shared_injector.compiled_cache_info()
    assert info2["current_size"] == 1

def test_cache_never_exceeds_256_and_lru_evicted():
    _shared_injector._clear_cache()
    vars = [PromptVariableDefinition(name="val")]
    
    for i in range(300):
        _shared_injector.render(body=f"T{i} {{{{ val }}}}", variables=vars, context={"val": "1"})
        
    info = _shared_injector.compiled_cache_info()
    assert info["current_size"] == 256
    assert info["evictions"] > 0
    assert info["misses"] == 300

    # Oldest T0 should be evicted, so fetching it again misses
    _shared_injector.render(body="T0 {{ val }}", variables=vars, context={"val": "1"})
    info2 = _shared_injector.compiled_cache_info()
    assert info2["misses"] == 301

def test_hit_changes_lru_order():
    _shared_injector._clear_cache()
    vars = [PromptVariableDefinition(name="val")]
    _shared_injector.render(body="T_Keep {{ val }}", variables=vars, context={"val": "1"})
    
    for i in range(255):
        _shared_injector.render(body=f"T{i} {{{{ val }}}}", variables=vars, context={"val": "1"})
        
    # Now size is 256. Hit T_Keep to make it most recent
    _shared_injector.render(body="T_Keep {{ val }}", variables=vars, context={"val": "1"})
    
    # Add one more to cause eviction of T0, not T_Keep
    _shared_injector.render(body="T_New {{ val }}", variables=vars, context={"val": "1"})
    
    # T_Keep should still be in cache (hit)
    info_before = _shared_injector.compiled_cache_info()
    _shared_injector.render(body="T_Keep {{ val }}", variables=vars, context={"val": "1"})
    info_after = _shared_injector.compiled_cache_info()
    assert info_after["hits"] > info_before["hits"]

def test_changed_source_creates_different_entry():
    _shared_injector._clear_cache()
    _shared_injector.render(body="A {{ val }}", variables=[PromptVariableDefinition(name="val")], context={"val":"1"})
    _shared_injector.render(body="B {{ val }}", variables=[PromptVariableDefinition(name="val")], context={"val":"1"})
    info = _shared_injector.compiled_cache_info()
    assert info["current_size"] == 2

def test_old_compiled_content_is_not_used_for_new_source():
    res = render_template_source(body="Old", variables=[], context={})
    res2 = render_template_source(body="New", variables=[], context={})
    assert res.body == "Old"
    assert res2.body == "New"

def test_cache_info_contains_no_source_or_context():
    info = _shared_injector.compiled_cache_info()
    assert "current_size" in info
    assert "hits" in info
    # Ensure no source keys
    assert not any(isinstance(v, str) for v in info.values())

# ---------------------------------------------------------
# 3. Deterministic Concurrent Rendering
# ---------------------------------------------------------

def test_deterministic_concurrent_rendering():
    results = []
    errors = []
    source = "Hello {{ id }}"
    vars = [PromptVariableDefinition(name="id")]
    lock = threading.Lock()
    
    def worker(i):
        try:
            res = render_template_source(body=source, variables=vars, context={"id": str(i)})
            with lock:
                results.append((i, res.body))
        except Exception as e:
            with lock:
                errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=5.0)

    assert all(not t.is_alive() for t in threads)
    assert len(errors) == 0
    assert len(results) == 50
    expected_outputs = {f"Hello {i}" for i in range(50)}
    actual_outputs = {body for _, body in results}
    assert actual_outputs == expected_outputs
    assert len(actual_outputs) == 50

# ---------------------------------------------------------
# 4. Managed Lookup Tests
# ---------------------------------------------------------

@patch("app.services.template_engine.ManagedPromptTemplateRegistry")
def test_managed_lookup_inputs(mock_registry_class):
    mock_registry = Mock()
    mock_registry_class.return_value = mock_registry
    mock_registry.find.return_value = PromptTemplateLookupResponse(
        id=1, name="t", agent_type="CommsAgent", channel="sms", language="en", status="assigned",
        body="Body", title="Title", variables=[], version=1, is_active=True, source="tenant"
    )
    
    render_managed_template(
        db=Mock(),
        tenant_id="tenant1",
        agent_type="CommsAgent",
        channel="sms",
        language="en",
        status="assigned",
        context={}
    )
    
    mock_registry.find.assert_called_with("CommsAgent", "sms", "en", "assigned")
    mock_registry_class.assert_called_once_with(db=mock_registry_class.call_args[1]["db"], tenant_id="tenant1", actor_id="system_renderer", redis_client=None)

def test_unsupported_format_raises_typed_error():
    with pytest.raises(UnsupportedTemplateFormatError) as exc_info:
        render_template_source(
            body="Hello",
            variables=[],
            context={},
            format="invalid_fmt"
        )
    assert "Template format is unsupported." in str(exc_info.value)
    assert "invalid_fmt" not in str(exc_info.value)

def test_database_fallback_stored_declarations_validation():
    """
    Validates that database fallback checks stored declarations against allowed_variable_paths.
    """
    from app.services.ai.guardrails.fallback_service import GuardrailFallbackService, FallbackTemplateSource
    from app.models import NotificationTemplate
    from app.services.ai.FieldOpsAI.schemas.communication import CommunicationContext

    # Create mock DB
    mock_db = Mock()

    # 1. Allowed variable 'customer_name' -> DB template renders
    mock_dto_allowed = PromptTemplateLookupResponse(
        id=10, name="Allowed DB", agent_type="CommsAgent", channel="sms", language="en", status="job_assigned",
        body="Hello {{ customer_name }}", title=None, variables=[{"name": "customer_name", "required": True}], version=1, is_active=True, source="tenant"
    )

    with patch("app.services.template_engine.ManagedPromptTemplateRegistry") as mock_reg_cls:
        reg_inst = Mock()
        reg_inst.find.return_value = mock_dto_allowed
        mock_reg_cls.return_value = reg_inst

        svc = GuardrailFallbackService(db=mock_db)
        ctx = CommunicationContext(job_id="1", notification_type="job_assigned", recipient_type="CUSTOMER", channel="SMS", locale="en", customer_name="Alice", job_status="ASSIGNED")
        res = svc.render(context=ctx)
        assert res.source == FallbackTemplateSource.DATABASE
        assert "Alice" in res.decision.message

    # 2. Disallowed variable 'additional_context' -> DB template rejected, falls to built-in
    mock_dto_disallowed = PromptTemplateLookupResponse(
        id=11, name="Disallowed DB", agent_type="CommsAgent", channel="sms", language="en", status="job_assigned",
        body="{{ additional_context }}", title=None, variables=[{"name": "additional_context", "required": True}], version=1, is_active=True, source="tenant"
    )

    with patch("app.services.template_engine.ManagedPromptTemplateRegistry") as mock_reg_cls:
        reg_inst = Mock()
        reg_inst.find.return_value = mock_dto_disallowed
        mock_reg_cls.return_value = reg_inst

        svc = GuardrailFallbackService(db=mock_db)
        ctx = CommunicationContext(job_id="1", notification_type="job_assigned", recipient_type="CUSTOMER", channel="SMS", locale="en", customer_name="Alice", job_status="ASSIGNED")
        res = svc.render(context=ctx)
        assert res.source != FallbackTemplateSource.DATABASE

# ---------------------------------------------------------
# 5. Format Tests
# ---------------------------------------------------------

@patch("app.services.template_engine.ManagedPromptTemplateRegistry")
def test_managed_html_template(mock_registry_class):
    mock_registry = Mock()
    mock_registry_class.return_value = mock_registry
    
    dto = PromptTemplateLookupResponse(
        id=1, name="t", agent_type="CommsAgent", channel="email", language="en", status="assigned",
        body="<b>{{ val }}</b>", title="Title", variables=[PromptVariableDefinition(name="val")], version=1, is_active=True, source="tenant", format="html"
    )
    mock_registry.find.return_value = dto
    
    res = render_managed_template(
        db=Mock(), tenant_id="tenant1", agent_type="CommsAgent", channel="email", language="en", status="assigned", context={"val": "<script>"}
    )
    assert res.body == "<b>&lt;script&gt;</b>"

@patch("app.services.template_engine.ManagedPromptTemplateRegistry")
def test_managed_text_template(mock_registry_class):
    mock_registry = Mock()
    mock_registry_class.return_value = mock_registry
    
    dto = PromptTemplateLookupResponse(
        id=1, name="t", agent_type="CommsAgent", channel="sms", language="en", status="assigned",
        body="{{ val }}", title="Title", variables=[PromptVariableDefinition(name="val")], version=1, is_active=True, source="tenant", format="text"
    )
    mock_registry.find.return_value = dto
    
    res = render_managed_template(
        db=Mock(), tenant_id="tenant1", agent_type="CommsAgent", channel="sms", language="en", status="assigned", context={"val": "<script>"}
    )
    assert res.body == "<script>"

def test_managed_invalid_format_dto_rejected():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        PromptTemplateLookupResponse(
            id=1, name="t", agent_type="CommsAgent", channel="email", language="en", status="active",
            body="<b>test</b>", title="Title", variables=[], version=1, is_active=True, source="tenant", format="markdown"
        )

def test_unsupported_format_rejection():
    with pytest.raises(UnsupportedTemplateFormatError):
        render_template_source(
            body="Body", variables=[], context={}, format="unknown"
        )

# ---------------------------------------------------------
# 6. Preview Tests
# ---------------------------------------------------------

@patch("app.services.template_engine.render_template_source")
def test_preview_performs_no_db_write(mock_rts):
    from app.services.template_engine import RenderedMessageResult
    mock_rts.return_value = RenderedMessageResult(title="T", body="B", template_id=None, template_version=None, source="preview", missing_optional_paths=set())
    # Just asserting it doesn't even take a db argument
    res = render_preview(title_template="T", body_template="B", context={})
    assert res["body"] == "B"
    
def test_unsafe_preview_returns_400():
    from app.routes.templates import preview_template
    from app.schemas import TemplatePreviewRequest
    req = TemplatePreviewRequest(title_template="T", body_template="{{ val.__class__ }}", mock_context={"val": "1"}, variables=[])
    import asyncio
    with pytest.raises(HTTPException) as exc:
        asyncio.run(preview_template(payload=req, authorization="test"))
    assert exc.value.status_code == 400

# ---------------------------------------------------------
# 7. Unsafe Jinja Matrix
# ---------------------------------------------------------

@pytest.mark.parametrize(
    "source",
    [
        "{{ name.__class__ }}",
        "{{ name['__class__'] }}",
        "{{ helper() }}",
        "{% import 'x.html' as x %}",
        "{% include 'x.html' %}",
        "{{ data[key] }}", # dynamic dict key
        "{{ value | unsupported_filter }}",
    ],
)
def test_genuine_unsafe_jinja_matrix(source):
    with pytest.raises((MessageTemplateRenderingError, InvalidTemplateSyntaxError)):
        render_template_source(
            body=source,
            title=None,
            variables=[PromptVariableDefinition(name="name"), PromptVariableDefinition(name="data")],
            context={"name": "Alice", "data": {"key": "val"}, "key": "key", "value": "1"}
        )

# ---------------------------------------------------------
# 8. Fallback Tests
# ---------------------------------------------------------

def test_fallback_module_has_no_local_injector():
    # We grep the file or inspect module
    import app.services.ai.guardrails.fallback_service as fs
    assert not hasattr(fs, "_shared_injector")


# ---------------------------------------------------------
# 9. RenderedMessageResult.resolved_locale tests (§3)
# ---------------------------------------------------------


def test_render_template_source_resolved_locale_is_none():
    """
    render_template_source operates on an in-memory template with no
    registry lookup, so resolved_locale must always be None.
    """
    res = render_template_source(
        body="Hello {{ name }}",
        variables=[PromptVariableDefinition(name="name")],
        context={"name": "Alice"},
        format="text",
    )
    assert res.resolved_locale is None


@patch("app.services.template_engine.ManagedPromptTemplateRegistry")
def test_rendered_result_exposes_resolved_locale(mock_registry_class):
    """
    render_managed_template must populate resolved_locale from the
    language field of the matched template DTO.
    """
    mock_registry = Mock()
    mock_registry_class.return_value = mock_registry
    mock_registry.find.return_value = PromptTemplateLookupResponse(
        id=1,
        name="t",
        agent_type="CommsAgent",
        channel="sms",
        language="en",
        status="assigned",
        body="Hello",
        title=None,
        variables=[],
        version=1,
        is_active=True,
        source="tenant",
    )

    res = render_managed_template(
        db=Mock(),
        tenant_id="tenant1",
        agent_type="CommsAgent",
        channel="sms",
        language="en",
        status="assigned",
        context={},
    )

    assert res.resolved_locale == "en"

def test_fallback_rejects_nested_disallowed_path():
    from app.services.ai.guardrails.fallback_service import (
        FallbackTemplateSource,
        GuardrailFallbackService,
    )
    from app.services.ai.FieldOpsAI.schemas.communication import (
        CommunicationContext,
    )

    disallowed_dto = (
        PromptTemplateLookupResponse(
            id=20,
            name="Nested disallowed",
            agent_type="CommsAgent",
            channel="sms",
            language="en",
            status="job_assigned",
            body="{{ customer_name.secret }}",
            title=None,
            variables=[
                {
                    "name": (
                        "customer_name.secret"
                    ),
                    "required": True,
                },
            ],
            version=1,
            is_active=True,
            source="tenant",
            format="text",
        )
    )

    with patch(
        "app.services.template_engine."
        "ManagedPromptTemplateRegistry"
    ) as registry_class:
        registry = Mock()
        registry.find.return_value = (
            disallowed_dto
        )
        registry_class.return_value = registry

        service = GuardrailFallbackService(
            db=Mock(),
        )

        context = CommunicationContext(
            job_id="1",
            notification_type=(
                "job_assigned"
            ),
            recipient_type="CUSTOMER",
            channel="SMS",
            locale="en",
            customer_name="Alice",
            job_status="ASSIGNED",
        )

        result = service.render(
            context=context,
        )

    assert result.source != (
        FallbackTemplateSource.DATABASE
    )