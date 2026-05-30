"""Validation helpers shared by the KB tools and the dispatch contract."""

from __future__ import annotations

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
