import pytest

from app.services.ai.FieldOpsAI.services.prompt_variable_injector import (
    InvalidPromptContextError,
    InvalidVariableDeclarationError,
    MissingRequiredVariableError,
    PromptRenderingError,
    PromptVariableInjector,
    UndeclaredTemplateVariableError,
    UnsafeTemplateExpressionError,
)


def test_safe_filters_and_missing_optional():
    injector = PromptVariableInjector()

    body = (
        "Hi {{ customer.name }}. "
        "ETA: {{ job.eta | upper }}. "
        "Notes: {{ notes }}"
    )

    variables = [
        {
            "name": "customer.name",
            "required": True,
        },
        {
            "name": "job.eta",
            "required": False,
            "default": "N/A",
        },
        {
            "name": "notes",
            "required": False,
            "default": None,
        },
    ]

    context = {
        "customer": {
            "name": "Alice",
        },
    }

    result = injector.render(
        body=body,
        variables=variables,
        context=context,
    )

    assert result.rendered_body == (
        "Hi Alice. ETA: N/A. Notes: "
    )

    assert (
        "job.eta"
        in result.missing_optional_paths
    )

    assert (
        "notes"
        in result.missing_optional_paths
    )


def test_missing_required_raises():
    injector = PromptVariableInjector()

    with pytest.raises(
        MissingRequiredVariableError
    ):
        injector.render(
            body="{{ customer.name }}",
            variables=[
                {
                    "name": "customer.name",
                    "required": True,
                }
            ],
            context={},
        )


def test_required_parent_missing_child_raises():
    injector = PromptVariableInjector()

    with pytest.raises(
        MissingRequiredVariableError
    ):
        injector.render(
            body="{{ customer.name }}",
            variables=["customer"],
            context={
                "customer": {},
            },
        )


def test_undeclared_reference_raises():
    injector = PromptVariableInjector()

    # A parent declaration authorizes its child path.
    result = injector.render(
        body="{{ customer.name }}",
        variables=["customer"],
        context={
            "customer": {
                "name": "Bob",
            }
        },
    )

    assert result.rendered_body == "Bob"

    # secret_token was not declared.
    with pytest.raises(
        UndeclaredTemplateVariableError
    ):
        injector.render(
            body=(
                "{{ customer.name }} "
                "{{ secret_token }}"
            ),
            variables=["customer"],
            context={
                "customer": {
                    "name": "Bob",
                },
                "secret_token": "secret",
            },
        )


def test_unsafe_expressions_rejected():
    injector = PromptVariableInjector()

    variables = ["customer"]

    context = {
        "customer": {},
    }

    unsafe_bodies = [
        (
            "{% for key, value in customer.items() %}"
            "{{ key }}"
            "{% endfor %}"
        ),
        "{{ customer.__class__ }}",
        "{{ customer['__class__'] }}",
        "{% import 'foo.html' as foo %}",
        "{{ [].__class__.__mro__[1] }}",
        "{{ customer.keys() }}",
    ]

    for body in unsafe_bodies:
        with pytest.raises(
            UnsafeTemplateExpressionError
        ):
            injector.render(
                body=body,
                variables=variables,
                context=context,
            )


def test_context_isolation():
    injector = PromptVariableInjector()

    context = {
        "customer": {
            "name": "Alice",
        },
        "secret": "12345",
    }

    result = injector.render(
        body="{{ customer.name }}",
        variables=["customer.name"],
        context=context,
    )

    assert result.rendered_body == "Alice"

    # The original input context must not be modified.
    assert context == {
        "customer": {
            "name": "Alice",
        },
        "secret": "12345",
    }


def test_date_formatting():
    injector = PromptVariableInjector()

    body = (
        "{{ job.created_at "
        "| format_date('iso') }}"
    )

    variables = [
        {
            "name": "job.created_at",
        }
    ]

    context = {
        "job": {
            "created_at": (
                "2026-07-21T10:00:00Z"
            ),
        }
    }

    result = injector.render(
        body=body,
        variables=variables,
        context=context,
    )

    assert result.rendered_body == (
        "2026-07-21T10:00:00+00:00"
    )

    invalid_body = (
        "{{ job.created_at "
        "| format_date('invalid_format') }}"
    )

    with pytest.raises(
        PromptRenderingError
    ):
        injector.render(
            body=invalid_body,
            variables=variables,
            context=context,
        )


def test_html_mode_escaping():
    injector = PromptVariableInjector()

    body = "{{ customer.name }}"

    variables = [
        {
            "name": "customer.name",
        }
    ]

    context = {
        "customer": {
            "name": (
                "<script>alert(1)</script>"
            ),
        }
    }

    html_result = injector.render(
        body=body,
        variables=variables,
        context=context,
        html=True,
    )

    assert (
        "&lt;script&gt;"
        in html_result.rendered_body
    )

    text_result = injector.render(
        body=body,
        variables=variables,
        context=context,
        html=False,
    )

    assert (
        "<script>"
        in text_result.rendered_body
    )


def test_size_limits():
    injector = PromptVariableInjector()

    with pytest.raises(
        PromptRenderingError
    ):
        injector.render(
            body="{{ big }}",
            variables=["big"],
            context={
                "big": "A" * 50001,
            },
        )


def test_ast_node_limit():
    injector = PromptVariableInjector()

    body = "".join(
        "{{ big }} "
        for _ in range(600)
    )

    with pytest.raises(
        UnsafeTemplateExpressionError,
        match="AST node count",
    ):
        injector.render(
            body=body,
            variables=["big"],
            context={
                "big": "A",
            },
        )


def test_unused_declaration_raises():
    injector = PromptVariableInjector()

    with pytest.raises(
        InvalidVariableDeclarationError
    ):
        injector.render(
            body="{{ customer.name }}",
            variables=[
                "customer.name",
                "unused_var",
            ],
            context={
                "customer": {
                    "name": "Alice",
                },
                "unused_var": "unused",
            },
        )


def test_public_inference_api():
    injector = PromptVariableInjector()

    declarations = (
        injector.infer_declarations(
            body=(
                "Hello {{ customer.name }}. "
                "Your ETA is {{ job.eta }}"
            ),
            title=(
                "Update for {{ customer.name }}"
            ),
        )
    )

    assert declarations == [
        "customer.name",
        "job.eta",
    ]


def test_unused_declaration_parent_and_child():
    injector = PromptVariableInjector()

    # A child declaration is unused when the
    # template references only its parent.
    with pytest.raises(
        InvalidVariableDeclarationError
    ):
        injector.validate(
            body="{{ customer }}",
            variables=[
                {
                    "name": (
                        "customer.middle_name"
                    ),
                }
            ],
        )

    # A parent declaration authorizes a child.
    definitions = injector.validate(
        body="{{ customer.name }}",
        variables=[
            {
                "name": "customer",
            }
        ],
    )

    assert len(definitions) == 1


def test_ast_filters_and_tests():
    injector = PromptVariableInjector()

    with pytest.raises(
        UnsafeTemplateExpressionError,
        match="Filter not allowed",
    ):
        injector._parse_ast(
            "{{ customer | unknown_filter }}"
        )

    with pytest.raises(
        UnsafeTemplateExpressionError,
        match="Filter not allowed",
    ):
        injector._parse_ast(
            "{{ customer | attr('name') }}"
        )

    with pytest.raises(
        UnsafeTemplateExpressionError,
        match="Filter not allowed",
    ):
        injector._parse_ast(
            (
                "{{ customer "
                "| map(attribute='name') }}"
            )
        )

    with pytest.raises(
        UnsafeTemplateExpressionError,
        match="Test not allowed",
    ):
        injector._parse_ast(
            "{{ value is callable }}"
        )


def test_context_conversion_protection():
    injector = PromptVariableInjector()

    oversized_dictionary = {
        f"key_{index}": index
        for index in range(1005)
    }

    with pytest.raises(
        InvalidPromptContextError,
        match="Collection size exceeded",
    ):
        injector._to_plain_structure(
            oversized_dictionary
        )

    with pytest.raises(
        InvalidPromptContextError,
        match="keys must be strings",
    ):
        injector._to_plain_structure(
            {
                1: "one",
            }
        )

    class FakeModel:
        def model_dump(self):
            raise AssertionError(
                "Fake model_dump must not execute."
            )

    with pytest.raises(
        InvalidPromptContextError,
        match="Unsupported context type",
    ):
        injector._to_plain_structure(
            FakeModel()
        )

    with pytest.raises(
        InvalidPromptContextError,
        match="NaN and Infinity",
    ):
        injector._to_plain_structure(
            float("inf")
        )

    with pytest.raises(
        InvalidPromptContextError,
        match="NaN and Infinity",
    ):
        injector._to_plain_structure(
            float("nan")
        )


def test_list_indexes():
    injector = PromptVariableInjector()

    # A valid static list index renders.
    result = injector.render(
        body="{{ technicians[0].name }}",
        variables=["technicians"],
        context={
            "technicians": [
                {
                    "name": "Bob",
                }
            ]
        },
    )

    assert result.rendered_body == "Bob"

    # Index 1 does not exist, so this is a
    # missing required path.
    with pytest.raises(
        MissingRequiredVariableError
    ):
        injector.render(
            body="{{ technicians[1].name }}",
            variables=["technicians"],
            context={
                "technicians": [
                    {
                        "name": "Bob",
                    }
                ]
            },
        )

    # Dynamic list indexes are forbidden.
    with pytest.raises(
        UnsafeTemplateExpressionError,
        match="Dynamic dictionary keys",
    ):
        injector._parse_ast(
            "{{ technicians[selected_index] }}"
        )


def test_inferred_static_list_path_renders():
    injector = PromptVariableInjector()

    declarations = (
        injector.infer_declarations(
            body="{{ technicians[0].name }}"
        )
    )

    assert declarations == [
        "technicians.0.name",
    ]

    result = injector.render(
        body="{{ technicians[0].name }}",
        variables=declarations,
        context={
            "technicians": [
                {
                    "name": "Bob",
                }
            ]
        },
    )

    assert result.rendered_body == "Bob"


def test_primitive_override_with_optional_default():
    injector = PromptVariableInjector()

    result = injector.render(
        body="{{ customer.name }}",
        variables=[
            {
                "name": "customer.name",
                "required": False,
                "default": "Default Name",
            }
        ],
        context={
            "customer": "PrimitiveString",
        },
    )

    assert result.rendered_body == (
        "Default Name"
    )