"""Provider schema shaping without changing local validation contracts."""

import json
from copy import deepcopy
from typing import Any

WIRE_NULL = "x-anthropic-null"


class NullableWireCodec:
    """Encode explicitly annotated nullable fields without provider union branches."""

    version = "nullable-sentinels-v1"

    def __init__(self, schema: dict[str, Any], nulls: dict[str, Any]) -> None:
        self.original = deepcopy(schema)
        for path, sentinel in nulls.items():
            field = self.original
            for part in path.split("/"):
                field = field[part]
            field[WIRE_NULL] = sentinel
        self.schema = deepcopy(self.original)
        self.fields = 0
        self._transform(self.schema)

    def _transform(self, node: Any) -> None:
        if isinstance(node, dict):
            if WIRE_NULL in node:
                sentinel = node.pop(WIRE_NULL)
                variants = node.pop("anyOf")
                nonnull = [item for item in variants if item.get("type") != "null"]
                if len(variants) != 2 or len(nonnull) != 1:
                    raise ValueError("wire_null_requires_one_nullable_type")
                node.update(nonnull[0])
                if "enum" in node:
                    node["enum"] = [*node["enum"], sentinel]
                # The empty representation intentionally bypasses these bounds;
                # the original Pydantic model checks restored nonempty values.
                for key in ("minimum", "minLength", "minItems"):
                    node.pop(key, None)
                node["default"] = sentinel
                node["description"] = (
                    node.get("description", "")
                    + f" Use {json.dumps(sentinel)} when unspecified; do not emit null."
                ).strip()
                self.fields += 1
            for child in node.values():
                self._transform(child)
        elif isinstance(node, list):
            for child in node:
                self._transform(child)

    def convert(self, value: Any, *, encode: bool = False) -> Any:
        """Restore only annotated paths, including repeated references and array items."""

        def visit(item: Any, spec: dict) -> Any:
            if "$ref" in spec:
                resolved = self.original
                for part in spec["$ref"].removeprefix("#/").split("/"):
                    resolved = resolved[part.replace("~1", "/").replace("~0", "~")]
                return visit(item, resolved)
            if WIRE_NULL in spec:
                if encode and item is None:
                    return deepcopy(spec[WIRE_NULL])
                if not encode and type(item) is type(spec[WIRE_NULL]) and item == spec[WIRE_NULL]:
                    return None
                spec = next(v for v in spec["anyOf"] if v.get("type") != "null")
            if isinstance(item, dict):
                properties = spec.get("properties", {})
                return {key: visit(val, properties.get(key, {})) for key, val in item.items()}
            if isinstance(item, list):
                return [visit(val, spec.get("items", {})) for val in item]
            return item

        return visit(value, self.original)


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
