"""Bounded validation diagnostics without response values or exception text."""

from __future__ import annotations

from pydantic import BaseModel, JsonValue, ValidationError

_ERROR_TYPES = frozenset(
    {
        "assertion_error",
        "bool_parsing",
        "bool_type",
        "dict_type",
        "enum",
        "extra_forbidden",
        "finite_number",
        "float_parsing",
        "float_type",
        "greater_than",
        "greater_than_equal",
        "int_from_float",
        "int_parsing",
        "int_type",
        "json_invalid",
        "json_type",
        "less_than",
        "less_than_equal",
        "list_type",
        "literal_error",
        "missing",
        "model_attributes_type",
        "model_type",
        "none_required",
        "recursion_loop",
        "string_pattern_mismatch",
        "string_too_long",
        "string_too_short",
        "string_type",
        "too_long",
        "too_short",
        "union_tag_invalid",
        "union_tag_not_found",
        "value_error",
    }
)

# Exact known messages map to fixed codes. Never publish the message itself, or
# format a message/context into a code. Model-layer code does not import the brain.
_RULES = {
    "A targeted action requires a current element ref": "action_target_required",
    "A text action requires a record or credential source": "text_source_required",
    "An action requires an executable intent and one action": "action_intent_mismatch",
    "Evidence and stop decisions cannot include UI actions": "nonaction_has_action",
    "Non-action decision intent must match its operation": "nonaction_intent_mismatch",
    "Cached input cannot exceed all input tokens": "cached_input_out_of_range",
}


def _schema_path_names(schema: type[BaseModel] | None) -> tuple[set[str], set[str]]:
    """Declared properties and definition keys, never titles/examples/defaults."""
    if schema is None:
        return set(), set()
    names: set[str] = set()
    branches: set[str] = set()
    pending: list[object] = [schema.model_json_schema()]
    while pending:
        node = pending.pop()
        if not isinstance(node, dict):
            continue
        properties = node.get("properties", {})
        if isinstance(properties, dict):
            names.update(key for key in properties if isinstance(key, str))
            pending.extend(properties.values())
        for key in ("$defs", "definitions", "dependentSchemas", "patternProperties"):
            values = node.get(key)
            if isinstance(values, dict):
                if key == "$defs":
                    branches.update(name for name in values if isinstance(name, str))
                pending.extend(values.values())
        for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
            values = node.get(key)
            if isinstance(values, list):
                pending.extend(values)
        for key in ("items", "additionalProperties", "contains", "not", "if", "then", "else"):
            pending.append(node.get(key))
    return names, branches


def safe_validation_errors(
    error: ValidationError, schema: type[BaseModel] | None = None
) -> list[dict[str, JsonValue]]:
    """Return only fixed error types/rules and trusted field/index locations.

    Trusted schema definition names are removed from union paths. Property names
    take precedence if they collide with a definition name. Unknown components
    invalidate the entire location: extra-field names, mapping keys and unknown
    union branch labels may originate in sensitive response data.
    With no schema, string locations are omitted. Values, messages, URLs and error
    contexts never enter the returned metadata. At most 20 errors are recorded.
    """
    try:
        fields, branches = _schema_path_names(schema)
    except Exception:
        # Metadata must not replace the original validation failure if schema
        # introspection is unavailable. No string locations are then trusted.
        fields, branches = set(), set()
    result: list[dict[str, JsonValue]] = []
    for entry in error.errors(include_url=False, include_context=False, include_input=False)[:20]:
        error_type = entry.get("type")
        safe_type = error_type if error_type in _ERROR_TYPES else "validation_error"
        raw_location = entry.get("loc", ())
        location: list[JsonValue] = []
        if len(raw_location) <= 16:
            for part in raw_location:
                if (type(part) is int and part >= 0) or (type(part) is str and part in fields):
                    location.append(part)
                elif type(part) is str and part in branches:
                    continue
                else:
                    location = []
                    break
        item: dict[str, JsonValue] = {"type": safe_type, "loc": location}
        message = entry.get("msg", "")
        if isinstance(message, str):
            rule = _RULES.get(message.removeprefix("Value error, "))
            if rule is not None:
                item["rule"] = rule
        result.append(item)
    return result
