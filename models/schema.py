"""Provider schema shaping without changing local validation contracts."""

import json
from copy import deepcopy
from typing import Any

WIRE_NULL = "x-anthropic-null"


class NullableWireCodec:
    """Encode explicitly annotated nullable fields without provider union branches."""

    version = "nullable-sentinels-v1"

    def __init__(
        self,
        schema: dict[str, Any],
        nulls: dict[str, Any],
        unions: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.original = deepcopy(schema)
        for path, sentinel in nulls.items():
            field = self.original
            for part in path.split("/"):
                field = field[part]
            field[WIRE_NULL] = sentinel
        self.schema = deepcopy(self.original)
        self.fields = 0
        self._transform(self.schema)
        self.union_defaults = {}
        for path, defaults in (unions or {}).items():
            original, wire = self.original, self.schema
            for part in path.split("/"):
                original, wire = original[part], wire[part]
            self.union_defaults[id(original)] = defaults
            self._flatten_union(wire, defaults)
        if unions:
            self.version = "nullable-sentinels-flat-actions-v2"

    def _flatten_union(self, node: dict, defaults: dict[str, Any]) -> None:
        """Flatten an opted-in tagged union only on the provider wire."""
        tag = node["discriminator"]["propertyName"]
        references = list(dict.fromkeys(node["discriminator"]["mapping"].values()))
        properties = {}
        used_by, required_by = {}, {}
        tags = []
        for reference in references:
            branch = self.schema["$defs"][reference.rsplit("/", 1)[-1]]
            tag_spec = branch["properties"][tag]
            branch_tags = tag_spec.get("enum", [tag_spec.get("const")])
            tags.extend(branch_tags)
            for name, spec in branch["properties"].items():
                if name == tag:
                    continue
                used_by.setdefault(name, []).extend(branch_tags)
                if name in branch.get("required", []):
                    required_by.setdefault(name, []).extend(branch_tags)
                if name not in properties:
                    properties[name] = deepcopy(spec)
                    properties[name].pop("description", None)
                if spec.get("description"):
                    properties[name]["description"] = (
                        properties[name].get("description", "")
                        + f" For {', '.join(branch_tags)}: {spec['description']}"
                    ).strip()
        for name, spec in properties.items():
            spec["description"] = (
                f"Used for actions: {', '.join(used_by[name])}. "
                f"Required for actions: {', '.join(required_by.get(name, [])) or 'none'}. "
                + spec.get("description", "")
            ).strip()
        properties = {tag: {"type": "string", "enum": tags}, **properties}
        for name, sentinel in defaults.items():
            spec = properties[name]
            # An unused action field carries its explicit sentinel on the flat wire.
            # The original tagged branch still enforces these bounds after decoding.
            for bound in ("minimum", "exclusiveMinimum", "minLength", "minItems"):
                spec.pop(bound, None)
            if "enum" in spec and sentinel not in spec["enum"]:
                spec["enum"].append(sentinel)
            spec["description"] = (
                spec.get("description", "")
                + f" Use {json.dumps(sentinel)} when this action does not use {name}."
            ).strip()
        node.clear()
        node.update(
            type="object",
            properties=properties,
            required=list(properties),
            additionalProperties=False,
        )
        # Remove only now-unreferenced branch definitions, preserving other contracts.
        serialized = json.dumps(self.schema)
        for reference in references:
            if json.dumps(reference) not in serialized:
                del self.schema["$defs"][reference.rsplit("/", 1)[-1]]

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
            if isinstance(item, dict) and "discriminator" in spec:
                discriminator = spec["discriminator"]
                reference = discriminator["mapping"].get(item.get(discriminator["propertyName"]))
                if reference is not None:
                    defaults = self.union_defaults.get(id(spec), {})
                    allowed = self.original["$defs"][reference.rsplit("/", 1)[-1]]["properties"]
                    item = dict(item)
                    if not encode:
                        for key, sentinel in defaults.items():
                            if (
                                key not in allowed
                                and type(item.get(key)) is type(sentinel)
                                and item.get(key) == sentinel
                            ):
                                item.pop(key)
                    result = visit(item, {"$ref": reference})
                    if encode:
                        for key, sentinel in defaults.items():
                            if key not in allowed:
                                result.setdefault(key, deepcopy(sentinel))
                    return result
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
