"""Form-data casting.

Reflex hands ``on_submit`` a ``dict[str, str]`` — every value is a string,
because that is what an HTML form produces. SQLModel wants real Python types.
``cast_form_data`` bridges the two by reading the target model's annotations,
so the form and the model can never drift apart: change a field's type on the
model and the cast follows automatically.

Usage::

    try:
        data = cast_form_data(form_data, ItemForm)
    except FormCastError as exc:
        self.form_errors = exc.errors      # {field: message}
        return                             # dialog stays open
"""

import types
from collections.abc import Mapping
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel

_TRUE = {"true", "on", "yes", "1", "y"}
_FALSE = {"false", "off", "no", "0", "n", ""}


class FormCastError(ValueError):
    """Raised when one or more form fields cannot be cast.

    Carries every failure at once so a form can show all its errors in one
    round trip instead of one per submit.
    """

    def __init__(self, errors: dict[str, str]) -> None:
        self.errors = errors
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    """Strip ``| None`` from an annotation.

    Returns the inner type and whether None was permitted. Handles both
    ``Optional[X]`` / ``Union[X, None]`` and PEP 604 ``X | None``.
    """
    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        args = [a for a in get_args(annotation) if a is not type(None)]
        optional = len(args) != len(get_args(annotation))
        if len(args) == 1:
            return args[0], optional
        return annotation, optional
    return annotation, False


def _cast_scalar(value: str, target: Any) -> Any:
    """Cast a single string to ``target``. Raises ValueError on failure."""
    if target is str or target is Any:
        return value
    if isinstance(target, type) and issubclass(target, Enum):
        try:
            return target(value)
        except ValueError:
            return target[value]  # fall back to member name
    if target is bool:
        lowered = value.strip().lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        raise ValueError(f"{value!r} is not a boolean")
    if target is int:
        return int(value.strip())
    if target is float:
        return float(value.strip())
    if target is Decimal:
        try:
            return Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValueError(f"{value!r} is not a decimal number") from exc
    if target is datetime:
        return datetime.fromisoformat(value)
    if target is date:
        return date.fromisoformat(value)
    if target is time:
        return time.fromisoformat(value)
    # Unknown target — hand the raw string over and let the model validate.
    return value


def cast_form_data(
    raw: Mapping[str, Any],
    model: type[BaseModel],
    *,
    ignore_unknown: bool = True,
) -> dict[str, Any]:
    """Coerce raw form strings into ``model``'s field types.

    Args:
        raw: The dict Reflex passes to an ``on_submit`` handler.
        model: Any pydantic/SQLModel class — the annotations are the spec.
        ignore_unknown: Drop keys the model does not declare. Forms routinely
            carry extras (submit button names, CSRF-ish fields); passing them
            through would blow up model construction.

    Returns:
        A dict ready for ``model(**data)``.

    Raises:
        FormCastError: One or more values could not be cast. ``.errors`` maps
            field name to a human-readable message.
    """
    fields = model.model_fields
    result: dict[str, Any] = {}
    errors: dict[str, str] = {}

    for key, value in raw.items():
        if key not in fields:
            if ignore_unknown:
                continue
            result[key] = value
            continue

        target, optional = _unwrap_optional(fields[key].annotation)

        # Checkboxes submit booleans directly; only strings need casting.
        if not isinstance(value, str):
            result[key] = value
            continue

        if value.strip() == "":
            if optional:
                # An untouched optional input submits "", which must become
                # None — including for `str | None`, or the column stores an
                # empty string where the model promised null.
                result[key] = None
            elif target is str:
                # Required text: hand "" to the model so its own rule
                # (min_length, a validator) produces the message.
                result[key] = value
            else:
                errors[key] = "This field is required."
            continue

        try:
            result[key] = _cast_scalar(value, target)
        except (ValueError, TypeError, KeyError):
            errors[key] = f"Expected {_type_name(target)}."

    if errors:
        raise FormCastError(errors)
    return result


def _type_name(target: Any) -> str:
    """Readable type name for error messages."""
    names = {
        int: "a whole number",
        float: "a number",
        Decimal: "a decimal number",
        bool: "true or false",
        date: "a date (YYYY-MM-DD)",
        datetime: "a date and time (ISO format)",
        time: "a time (HH:MM)",
    }
    if target in names:
        return names[target]
    return getattr(target, "__name__", str(target))
