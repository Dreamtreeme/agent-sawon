"""Provider schema shaping without changing local validation contracts."""

from typing import Any


def compact_provider_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Copy a schema without string/collection upper bounds used by local validators."""

    def compact(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                key: compact(value)
                for key, value in node.items()
                if key not in {"maxItems", "maxLength"}
            }
        if isinstance(node, list):
            return [compact(value) for value in node]
        return node

    return compact(schema)
