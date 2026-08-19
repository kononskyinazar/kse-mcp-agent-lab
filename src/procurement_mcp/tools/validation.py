"""Argument checking at the tool boundary.

MCP clients are not obliged to validate against the advertised schema, so the
server does it itself. An unknown argument is rejected rather than ignored:
silently dropping one would let a caller believe it had influenced a result it
did not touch.
"""

from __future__ import annotations

from typing import Any

from ..errors import invalid_input

_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": (int, float),
    "array": list,
    "object": dict,
}


def check_arguments(arguments: dict[str, Any], schema: dict[str, Any], *, tool: str) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise invalid_input(f"{tool} expects an object of arguments", tool=tool)

    properties: dict[str, Any] = schema.get("properties") or {}
    if schema.get("additionalProperties") is False:
        unknown = sorted(set(arguments) - set(properties))
        if unknown:
            raise invalid_input(
                f"{tool} received unknown argument(s): {', '.join(unknown)}",
                tool=tool,
                unknown_arguments=unknown,
                accepted_arguments=sorted(properties),
            )

    for name in schema.get("required") or []:
        if arguments.get(name) in (None, ""):
            raise invalid_input(f"{name} is required", field=name, tool=tool)

    resolved: dict[str, Any] = {}
    for name, spec in properties.items():
        if name not in arguments or arguments[name] is None:
            if "default" in spec:
                resolved[name] = spec["default"]
            continue
        resolved[name] = _check_value(arguments[name], spec, field=name, tool=tool)
    return resolved


def _check_value(value: Any, spec: dict[str, Any], *, field: str, tool: str) -> Any:
    expected = spec.get("type")
    if isinstance(expected, str):
        python_type = _TYPES.get(expected)
        # bool is an int in Python; an integer field must not accept True.
        if python_type and (
            not isinstance(value, python_type)
            or (expected in {"integer", "number"} and isinstance(value, bool))
        ):
            raise invalid_input(
                f"{field} must be of type {expected}",
                field=field,
                tool=tool,
                received=type(value).__name__,
            )

    if "enum" in spec and value not in spec["enum"]:
        raise invalid_input(
            f"{field} must be one of {spec['enum']}", field=field, tool=tool, received=value
        )
    if "minimum" in spec and value < spec["minimum"]:
        raise invalid_input(f"{field} must be >= {spec['minimum']}", field=field, tool=tool, received=value)
    if "maximum" in spec and value > spec["maximum"]:
        raise invalid_input(f"{field} must be <= {spec['maximum']}", field=field, tool=tool, received=value)
    if "minLength" in spec and len(value) < spec["minLength"]:
        raise invalid_input(
            f"{field} must be at least {spec['minLength']} characters", field=field, tool=tool
        )
    if "maxLength" in spec and len(value) > spec["maxLength"]:
        raise invalid_input(
            f"{field} must be at most {spec['maxLength']} characters", field=field, tool=tool
        )
    if "maxItems" in spec and len(value) > spec["maxItems"]:
        raise invalid_input(f"{field} must contain at most {spec['maxItems']} items", field=field, tool=tool)
    if spec.get("type") == "array" and "items" in spec:
        return [_check_value(item, spec["items"], field=f"{field}[]", tool=tool) for item in value]
    return value
