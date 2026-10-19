"""JSON Schema validation helper shared by all pipeline stages."""

import json
from functools import cache
from typing import Any

from jsonschema import Draft202012Validator

from vnpipe import SCHEMA_DIR


class SchemaError(ValueError):
    """Raised when an instance does not conform to its schema."""


@cache
def _validator(schema_name: str) -> Draft202012Validator:
    path = SCHEMA_DIR / f"{schema_name}.schema.json"
    if not path.is_file():
        raise FileNotFoundError(f"unknown schema {schema_name!r}: {path} does not exist")
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate(instance: Any, schema_name: str) -> None:
    """Validate ``instance`` against ``SCHEMA_DIR/<schema_name>.schema.json``.

    Raises ``SchemaError`` listing every problem found (path and message).
    """
    errors = sorted(_validator(schema_name).iter_errors(instance), key=lambda e: list(e.path))
    if not errors:
        return
    problems = [f"  at /{'/'.join(str(p) for p in e.absolute_path)}: {e.message}" for e in errors]
    raise SchemaError(
        f"{len(errors)} problem(s) validating against {schema_name!r}:\n" + "\n".join(problems)
    )
