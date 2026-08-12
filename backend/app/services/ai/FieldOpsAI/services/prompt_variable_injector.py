import re
from datetime import date, datetime
from typing import Any
import hashlib
from collections import OrderedDict
from threading import RLock

from jinja2 import StrictUndefined, TemplateSyntaxError, meta, nodes
from jinja2.sandbox import SandboxedEnvironment
from jinja2.visitor import NodeVisitor
from jinja2 import Template
from pydantic import BaseModel

from app.services.ai.FieldOpsAI.schemas.prompt_variable import PromptVariableDefinition, PromptVariableDeclaration

class PromptVariableInjectionError(Exception): pass
class InvalidVariableDeclarationError(PromptVariableInjectionError): pass
class InvalidTemplateSyntaxError(PromptVariableInjectionError): pass
class UnsafeTemplateExpressionError(PromptVariableInjectionError): pass
class UndeclaredTemplateVariableError(PromptVariableInjectionError): pass
class MissingRequiredVariableError(PromptVariableInjectionError): pass
class InvalidPromptContextError(PromptVariableInjectionError): pass
class PromptRenderingError(PromptVariableInjectionError): pass

class PromptVariableInjectorResult(BaseModel):
    rendered_title: str | None
    rendered_body: str
    used_variable_paths: set[str]
    missing_optional_paths: set[str]

def _safe_finalize(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("none", "null", "undefined"):
            return ""
    return str(value)

def _format_datetime(value: Any, format_str: str = "datetime") -> str:
    if not isinstance(format_str, str):
        raise PromptRenderingError("Format string must be text")
    
    if format_str not in {"date", "date_short", "datetime", "datetime_short", "iso"}:
        raise PromptRenderingError(f"Unsupported format string: {format_str}")
        
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            raise PromptRenderingError("Invalid date string")
            
    if isinstance(value, (datetime, date)):
        try:
            if format_str == "iso":
                return value.isoformat()
            if format_str == "date":
                return value.strftime("%Y-%m-%d")
            if format_str == "date_short":
                return value.strftime("%m/%d/%Y")
            if format_str == "datetime":
                return value.strftime("%Y-%m-%d %H:%M:%S")
            if format_str == "datetime_short":
                return value.strftime("%m/%d/%Y %H:%M")
        except Exception:
            raise PromptRenderingError("Date formatting failed")
    raise PromptRenderingError("Invalid date object")

def _format_date(value: Any, format_str: str = "date") -> str:
    return _format_datetime(value, format_str)

_ALLOWED_NODE_TYPES = (
    nodes.Template, nodes.Output, nodes.TemplateData, nodes.Name, 
    nodes.Getattr, nodes.Getitem, nodes.If, nodes.CondExpr, nodes.Compare,
    nodes.Operand, nodes.Test, nodes.Filter, nodes.Const, nodes.List, 
    nodes.Dict, nodes.Tuple, nodes.Pair, nodes.BinExpr, nodes.UnaryExpr, 
    nodes.Concat, nodes.And, nodes.Or, nodes.Not
)

class PathVisitor(NodeVisitor):
    def __init__(self) -> None:
        self.paths: set[str] = set()

    def visit_Getattr(self, node: nodes.Getattr) -> None:
        path = self._resolve_path(node)
        if path:
            self.paths.add(path)
        else:
            self.generic_visit(node)
        
    def visit_Getitem(self, node: nodes.Getitem) -> None:
        path = self._resolve_path(node)
        if path:
            self.paths.add(path)
        else:
            self.generic_visit(node)
        
    def visit_Name(self, node: nodes.Name) -> None:
        if node.ctx == 'load':
            self.paths.add(node.name)
        
    def _resolve_path(self, node: nodes.Node) -> str | None:
        if isinstance(node, nodes.Name):
            return node.name
        elif isinstance(node, nodes.Getattr):
            left = self._resolve_path(node.node)
            if left:
                return f"{left}.{node.attr}"
        elif isinstance(node, nodes.Getitem):
            left = self._resolve_path(node.node)
            if left and isinstance(node.arg, nodes.Const) and isinstance(node.arg.value, (str, int)):
                return f"{left}.{node.arg.value}"
        return None

class PromptVariableInjector:
    MAX_RENDER_LENGTH = 50000
    MAX_TITLE_LENGTH = 500
    MAX_BODY_LENGTH = 50000
    MAX_AST_NODES = 500
    MAX_CONTEXT_DEPTH = 10
    MAX_COLLECTION_SIZE = 1000
    MAX_COMPILED_TEMPLATES = 256

    _compiled_cache: OrderedDict[str, Template] = OrderedDict()
    _cache_lock: RLock = RLock()
    _cache_hits = 0
    _cache_misses = 0
    _cache_evictions = 0

    def __init__(self) -> None:
        self._text_env = self._build_env(autoescape=False)
        self._html_env = self._build_env(autoescape=True)

    @classmethod
    def compiled_cache_info(cls) -> dict[str, int]:
        with cls._cache_lock:
            return {
                "max_size": cls.MAX_COMPILED_TEMPLATES,
                "current_size": len(cls._compiled_cache),
                "hits": cls._cache_hits,
                "misses": cls._cache_misses,
                "evictions": cls._cache_evictions,
            }

    @classmethod
    def _clear_cache(cls) -> None:
        with cls._cache_lock:
            cls._compiled_cache.clear()
            cls._cache_hits = 0
            cls._cache_misses = 0
            cls._cache_evictions = 0

    def _build_env(self, autoescape: bool) -> SandboxedEnvironment:
        env = SandboxedEnvironment(
            undefined=StrictUndefined,
            finalize=_safe_finalize,
            autoescape=autoescape
        )
        env.globals.clear()

        allowed_filters = {
            "default", "lower", "upper", "title", "capitalize", "trim", 
            "replace", "join", "length", "int", "float", "round", "escape", "e"
        }
        env.filters = {k: v for k, v in env.filters.items() if k in allowed_filters}
        env.filters["format_date"] = _format_date
        env.filters["format_datetime"] = _format_datetime

        allowed_tests = {
            "defined", "undefined", "none", "boolean", "string", 
            "number", "mapping", "sequence", "equalto"
        }
        env.tests = {k: v for k, v in env.tests.items() if k in allowed_tests}
        
        return env

    def _build_cache_key(self, source: str, html: bool) -> str:
        source_sha256 = hashlib.sha256(source.encode("utf-8")).hexdigest()
        mode = "html" if html else "text"
        policy_version = "1"
        return f"{source_sha256}:{mode}:{policy_version}"

    def _get_or_compile(self, source: str, html: bool) -> Template:
        cache_key = self._build_cache_key(source, html)

        with self._cache_lock:
            if cache_key in self._compiled_cache:
                self.__class__._cache_hits += 1
                template = self._compiled_cache.pop(cache_key)
                self._compiled_cache[cache_key] = template
                return template

            self.__class__._cache_misses += 1

        env = self._html_env if html else self._text_env
        try:
            compiled = env.from_string(source)
        except Exception as e:
            raise InvalidTemplateSyntaxError("Invalid Jinja syntax.") from None

        with self._cache_lock:
            if cache_key in self._compiled_cache:
                self._compiled_cache.pop(cache_key)
            self._compiled_cache[cache_key] = compiled
            
            if len(self._compiled_cache) > self.MAX_COMPILED_TEMPLATES:
                self._compiled_cache.popitem(last=False)
                self.__class__._cache_evictions += 1

        return compiled

    def infer_declarations(self, *, body: str, title: str | None = None) -> list[str]:
        if not isinstance(body, str):
            raise InvalidTemplateSyntaxError("Body must be a string")
        if len(body) > self.MAX_BODY_LENGTH:
            raise InvalidTemplateSyntaxError("Body exceeds maximum length")
        if title and not isinstance(title, str):
            raise InvalidTemplateSyntaxError("Title must be a string")
        if title and len(title) > self.MAX_TITLE_LENGTH:
            raise InvalidTemplateSyntaxError("Title exceeds maximum length")
            
        parsed_body = self._parse_ast(body)
        paths = self._extract_paths(parsed_body)
        if title:
            parsed_title = self._parse_ast(title)
            paths.update(self._extract_paths(parsed_title))
            
        return sorted(list(paths))

    def validate(self, body: str, variables: list[PromptVariableDeclaration], title: str | None = None) -> list[PromptVariableDefinition]:
        if not isinstance(body, str):
            raise InvalidTemplateSyntaxError("Body must be a string")
        if len(body) > self.MAX_BODY_LENGTH:
            raise InvalidTemplateSyntaxError("Body exceeds maximum length")
        if title and not isinstance(title, str):
            raise InvalidTemplateSyntaxError("Title must be a string")
        if title and len(title) > self.MAX_TITLE_LENGTH:
            raise InvalidTemplateSyntaxError("Title exceeds maximum length")

        defs = self._normalize_declarations(variables)
        
        seen = set()
        for d in defs:
            if d.name in seen:
                raise InvalidVariableDeclarationError(f"Duplicate variable names: {d.name}")
            seen.add(d.name)
            
            if not d.name or ".." in d.name or d.name.startswith(".") or d.name.endswith("."):
                raise InvalidVariableDeclarationError(f"Invalid path syntax: {d.name}")
            segments = d.name.split(".")

            for index, segment in enumerate(segments):
                if index > 0 and segment.isdigit():
                    continue
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", segment):
                    raise InvalidVariableDeclarationError(f"Invalid identifier segment: {segment}")
                if "__" in segment:
                    raise InvalidVariableDeclarationError(f"Dunder names are rejected: {d.name}")

        parsed_body = self._parse_ast(body)
        paths = self._extract_paths(parsed_body)
        
        if title:
            parsed_title = self._parse_ast(title)
            paths.update(self._extract_paths(parsed_title))

        for d in defs:
            used = any(self._is_prefix(d.name, p) for p in paths)
            if not used:
                raise InvalidVariableDeclarationError(f"Declared variables are not referenced in template: {d.name}")

        for path in paths:
            if not self._get_longest_match(path, defs):
                raise UndeclaredTemplateVariableError(f"Undeclared variables referenced in template: {path}")

        return defs

    def render(self, body: str, variables: list[PromptVariableDeclaration], context: dict[str, Any], title: str | None = None, html: bool = False) -> PromptVariableInjectorResult:
        defs = self.validate(body, variables, title)
        
        parsed_body = self._parse_ast(body)
        paths = self._extract_paths(parsed_body)
        if title:
            parsed_title = self._parse_ast(title)
            paths.update(self._extract_paths(parsed_title))

        render_ctx, missing_optional = self._build_render_context(context, defs, paths)

        compiled_body = self._get_or_compile(body, html)
        compiled_title = None
        if title:
            compiled_title = self._get_or_compile(title, False)

        try:
            rendered_title = compiled_title.render(**render_ctx) if compiled_title else None
            rendered_body = compiled_body.render(**render_ctx)
        except Exception as e:
            raise PromptRenderingError(f"Template rendering failed: {type(e).__name__}") from None

        if len(rendered_body) > self.MAX_RENDER_LENGTH or (rendered_title and len(rendered_title) > self.MAX_RENDER_LENGTH):
            raise PromptRenderingError("Rendered output exceeded size limits.")

        return PromptVariableInjectorResult(
            rendered_title=rendered_title,
            rendered_body=rendered_body,
            used_variable_paths=paths,
            missing_optional_paths=missing_optional
        )

    def _normalize_declarations(self, variables: list[PromptVariableDeclaration]) -> list[PromptVariableDefinition]:
        defs = []
        for v in variables:
            if isinstance(v, str):
                defs.append(PromptVariableDefinition(name=v))
            elif isinstance(v, dict):
                defs.append(PromptVariableDefinition(**v))
            else:
                defs.append(v)
        return defs

    def _parse_ast(self, source: str) -> nodes.Template:
        try:
            parsed = self._text_env.parse(source)
        except TemplateSyntaxError:
            raise InvalidTemplateSyntaxError("Invalid Jinja syntax.") from None

        node_count = 0
        _ALLOWED_FILTERS = {"default", "lower", "upper", "title", "capitalize", "trim", "replace", "join", "length", "int", "float", "round", "escape", "e", "format_date", "format_datetime"}
        _ALLOWED_TESTS = {"defined", "undefined", "none", "boolean", "string", "number", "mapping", "sequence", "equalto"}

        for node in parsed.find_all(nodes.Node):
            node_count += 1
            if node_count > self.MAX_AST_NODES:
                raise UnsafeTemplateExpressionError("Template exceeds maximum AST node count.")
            if not isinstance(node, _ALLOWED_NODE_TYPES):
                raise UnsafeTemplateExpressionError(f"Unsafe Jinja syntax is not permitted: {type(node).__name__}")
            if isinstance(node, nodes.Filter):
                if getattr(node, "name", None) not in _ALLOWED_FILTERS:
                    raise UnsafeTemplateExpressionError(f"Filter not allowed: {getattr(node, 'name', 'unknown')}")
            if isinstance(node, nodes.Test):
                if getattr(node, "name", None) not in _ALLOWED_TESTS:
                    raise UnsafeTemplateExpressionError(f"Test not allowed: {getattr(node, 'name', 'unknown')}")
            if isinstance(node, nodes.Getattr):
                if "__" in node.attr:
                    raise UnsafeTemplateExpressionError("Unsafe Jinja access is not permitted.")
            if isinstance(node, nodes.Getitem):
                if not isinstance(node.arg, nodes.Const):
                    raise UnsafeTemplateExpressionError("Dynamic dictionary keys are not permitted.")
                if isinstance(node.arg.value, int):
                    if node.arg.value < 0:
                        raise UnsafeTemplateExpressionError("Negative list indexes are not permitted.")
                elif isinstance(node.arg.value, str):
                    if "__" in node.arg.value:
                        raise UnsafeTemplateExpressionError("Unsafe Jinja access is not permitted.")
                else:
                    raise UnsafeTemplateExpressionError("Unsafe Getitem argument type.")
        return parsed

    def _extract_paths(self, ast: nodes.Template) -> set[str]:
        visitor = PathVisitor()
        visitor.visit(ast)
        return visitor.paths

    def _is_prefix(self, prefix: str, path: str) -> bool:
        return path == prefix or path.startswith(prefix + ".")

    def _get_longest_match(self, path: str, defs: list[PromptVariableDefinition]) -> PromptVariableDefinition | None:
        best_match = None
        best_len = -1
        for d in defs:
            if self._is_prefix(d.name, path):
                length = len(d.name)
                if length > best_len:
                    best_len = length
                    best_match = d
        return best_match

    def _to_plain_structure(self, obj: Any, depth: int = 0) -> Any:
        import math
        import dataclasses
        from pydantic import BaseModel
        if depth > self.MAX_CONTEXT_DEPTH:
            raise InvalidPromptContextError("Context nesting depth exceeded.")
        if obj is None:
            return None
        if isinstance(obj, bool):
            return obj
        if isinstance(obj, (int, float)):
            if isinstance(obj, float) and not math.isfinite(obj):
                raise InvalidPromptContextError("NaN and Infinity are rejected.")
            return obj
        if isinstance(obj, str):
            return obj
        if isinstance(obj, (date, datetime)):
            return obj
        if isinstance(obj, dict):
            if len(obj) > self.MAX_COLLECTION_SIZE:
                raise InvalidPromptContextError("Collection size exceeded.")
            for k in obj:
                if not isinstance(k, str):
                    raise InvalidPromptContextError("Dictionary keys must be strings.")
            return {k: self._to_plain_structure(v, depth + 1) for k, v in obj.items()}
        if isinstance(obj, list):
            if len(obj) > self.MAX_COLLECTION_SIZE:
                raise InvalidPromptContextError("Collection size exceeded.")
            return [self._to_plain_structure(v, depth + 1) for v in obj]
        if isinstance(obj, BaseModel):
            return self._to_plain_structure(obj.model_dump(mode="python"), depth + 1)
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            return self._to_plain_structure(dataclasses.asdict(obj), depth + 1)
        raise InvalidPromptContextError(f"Unsupported context type: {type(obj).__name__}")

    def _get_path(self, ctx_dict: dict[str, Any], path: str) -> tuple[Any, bool]:
        segments = path.split('.')
        current = ctx_dict
        for seg in segments:
            if isinstance(current, dict) and seg in current:
                current = current[seg]
            elif isinstance(current, list) and seg.isdigit():
                idx = int(seg)
                if 0 <= idx < len(current):
                    current = current[idx]
                else:
                    return None, False
            else:
                return None, False
        return current, True

    def _set_path_safe(self, ctx_dict: dict[str, Any], path: str, value: Any) -> None:
        segments = path.split('.')
        current = ctx_dict
        for i, seg in enumerate(segments[:-1]):
            next_seg = segments[i+1]
            if isinstance(current, list):
                if seg.isdigit():
                    idx = int(seg)
                    if idx >= len(current):
                        return
                    if current[idx] is None or not isinstance(current[idx], (dict, list)):
                        current[idx] = [] if next_seg.isdigit() else {}
                    current = current[idx]
                else:
                    return
            else:
                if seg not in current or current[seg] is None or not isinstance(current[seg], (dict, list)):
                    current[seg] = [] if next_seg.isdigit() else {}
                current = current[seg]
        
        last_seg = segments[-1]
        if isinstance(current, list):
            if last_seg.isdigit():
                idx = int(last_seg)
                if idx < len(current):
                    current[idx] = value
        else:
            current[last_seg] = value

    def _build_render_context(
        self,
        input_context: dict[str, Any],
        defs: list[PromptVariableDefinition],
        paths: set[str],
    ) -> tuple[dict[str, Any], set[str]]:
        render_context: dict[str, Any] = {}
        missing_optional: set[str] = set()

        authorized_top_level = {
            definition.name.split(".")[0]
            for definition in defs
        }

        for key in authorized_top_level:
            if key in input_context:
                render_context[key] = (
                    self._to_plain_structure(
                        input_context[key]
                    )
                )

        all_paths_to_check = (
            paths
            | {
                definition.name
                for definition in defs
            }
        )

        sorted_paths = sorted(
            all_paths_to_check,
            key=lambda path: (
                path.count("."),
                path,
            ),
        )

        for path in sorted_paths:
            declaration = self._get_longest_match(
                path,
                defs,
            )

            if declaration is None:
                continue

            value, exists = self._get_path(
                render_context,
                path,
            )

            if exists and value is not None:
                continue

            if (
                path in paths
                and declaration.required
            ):
                raise MissingRequiredVariableError(
                    f"Missing required path: {path}"
                )

            if not declaration.required:
                self._set_path_safe(
                    render_context,
                    path,
                    declaration.default,
                )

                missing_optional.add(path)

        return (
            render_context,
            missing_optional,
        )


def test_inferred_static_list_path_renders():
    injector = PromptVariableInjector()

    declarations = injector.infer_declarations(
        body="{{ technicians[0].name }}"
    )

    assert declarations == [
        "technicians.0.name"
    ]

    result = injector.render(
        body="{{ technicians[0].name }}",
        variables=declarations,
        context={
            "technicians": [
                {
                    "name": "Bob"
                }
            ]
        },
    )

    assert result.rendered_body == "Bob"