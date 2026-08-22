"""Validation helpers.

Two error sources have to end up looking the same to the UI: our own
``FormCastError`` (the string could not become an int) and pydantic's
``ValidationError`` (the int was out of range). Both collapse to
``dict[field, message]`` so a dialog only ever renders one shape.
"""

from typing import Any

from pydantic import BaseModel, ValidationError

from .casting import FormCastError, cast_form_data


def errors_from_validation(exc: ValidationError) -> dict[str, str]:
    """Flatten a pydantic ValidationError into ``{field: message}``.

    Nested fields are dotted (``address.zip``). Only the first error per field
    is kept — showing three messages under one input helps nobody.
    """
    errors: dict[str, str] = {}
    for err in exc.errors():
        field = ".".join(str(p) for p in err["loc"]) or "__root__"
        errors.setdefault(field, err["msg"])
    return errors


def parse_form[T: BaseModel](
    raw: dict[str, Any], model: type[T]
) -> tuple[T | None, dict[str, str]]:
    """Cast and validate form data in one step.

    Returns ``(instance, {})`` on success or ``(None, errors)`` on failure, so
    callers never need a try/except:

    >>> item, errors = parse_form(form_data, ItemForm)
    >>> if errors:
    ...     self.form_errors = errors
    ...     return
    """
    try:
        data = cast_form_data(raw, model)
    except FormCastError as exc:
        return None, exc.errors

    try:
        return model(**data), {}
    except ValidationError as exc:
        return None, errors_from_validation(exc)
