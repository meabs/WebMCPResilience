"""Schema-derived inputs; no application-specific value corpus is embedded."""
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis_jsonschema import from_schema


def generate_arguments(schema: dict[str, Any], examples: int) -> list[dict[str, Any]]:
    """Generate a bounded, reproducible-size set from a discovered JSON Schema."""
    if schema.get("type") != "object":
        raise ValueError("fuzzing requires an object input schema")
    values: list[dict[str, Any]] = []

    @settings(max_examples=examples, database=None, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(from_schema(schema))
    def collect(value: dict[str, Any]) -> None:
        values.append(value)

    collect()
    return values
