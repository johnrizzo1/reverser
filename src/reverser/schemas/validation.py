"""Validation helpers shared by the KB tools and the dispatch contract."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Type

from pydantic import BaseModel, ValidationError


@dataclass
class ValidationOutcome:
    ok: bool
    value: BaseModel | None
    error_text: str | None


def render_errors(exc: ValidationError) -> str:
    """Render a ValidationError as one actionable line per problem."""
    lines: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        lines.append(f"✗ {loc}: {err['msg']}")
    header = (
        "Validation failed. Fix the following and resubmit this call "
        "(do not give up — correct the fields):"
    )
    return header + "\n" + "\n".join(lines)


def validate_args(model: Type[BaseModel], args: dict) -> ValidationOutcome:
    """Parse args into the model. On failure return actionable error text."""
    try:
        instance = model(**args)
    except ValidationError as exc:
        return ValidationOutcome(ok=False, value=None, error_text=render_errors(exc))
    return ValidationOutcome(ok=True, value=instance, error_text=None)


def _inline_refs(node, defs):
    """Recursively replace {"$ref": "#/$defs/X"} with the resolved definition."""
    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"]
            name = ref.split("/")[-1]
            resolved = copy.deepcopy(defs.get(name, {}))
            # merge sibling keys (e.g. description) over the resolved body
            for k, v in node.items():
                if k != "$ref":
                    resolved[k] = v
            return _inline_refs(resolved, defs)
        return {k: _inline_refs(v, defs) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline_refs(item, defs) for item in node]
    return node


_INTERNAL_FIELDS = {"validated", "from_status"}


def tool_input_schema(model: Type[BaseModel]) -> dict:
    """Return a self-contained @tool input schema derived from a Pydantic model.

    Inlines all $ref/$defs, drops internal-only fields, and guarantees an
    object schema with a `required` list (claude-agent-sdk @tool shape).
    """
    raw = model.model_json_schema()
    defs = raw.get("$defs", {})
    inlined = _inline_refs({k: v for k, v in raw.items() if k != "$defs"}, defs)

    props = inlined.get("properties", {})
    for field in _INTERNAL_FIELDS:
        props.pop(field, None)
    required = [r for r in inlined.get("required", []) if r not in _INTERNAL_FIELDS]

    return {
        "type": "object",
        "properties": props,
        "required": required,
    }
