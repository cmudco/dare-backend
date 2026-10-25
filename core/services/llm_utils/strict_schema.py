"""Pydantic model -> self-contained JSON schema for native structured output."""

from typing import Any, Type

from pydantic import BaseModel

_DROPPED_KEYWORDS = ("title", "default")


def strict_json_schema(model: Type[BaseModel]) -> dict[str, Any]:
    """Inline ``$ref``s, close every object, and require every property.

    Claude rejects objects without ``additionalProperties: false``, and
    Gemini's response schema cannot follow ``$ref``.
    """
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def walk(node):
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return walk(definitions[node["$ref"].rsplit("/", 1)[-1]])
        result = {}
        for key, value in node.items():
            if key == "properties":
                result[key] = {name: walk(field) for name, field in value.items()}
            elif key not in _DROPPED_KEYWORDS:
                result[key] = walk(value)
        if result.get("type") == "object" and "properties" in result:
            result["additionalProperties"] = False
            result["required"] = list(result["properties"])
        return result

    return walk(schema)
