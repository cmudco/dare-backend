from typing import List, Literal, Optional

from django.test import SimpleTestCase
from pydantic import BaseModel

from core.services.llm_utils.strict_schema import strict_json_schema


class _Item(BaseModel):
    title: str
    kind: Literal["a", "b"]
    note: Optional[str] = None


class _Answer(BaseModel):
    items: List[_Item]
    explicit: bool = False


class StrictJsonSchemaTests(SimpleTestCase):
    def test_nested_models_are_inlined_closed_and_fully_required(self):
        schema = strict_json_schema(_Answer)
        item = schema["properties"]["items"]["items"]

        self.assertNotIn("$defs", schema)
        self.assertEqual(schema["required"], ["items", "explicit"])
        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(item["additionalProperties"])
        self.assertEqual(item["required"], ["title", "kind", "note"])
        self.assertNotIn("default", schema["properties"]["explicit"])

    def test_a_field_named_title_survives_keyword_stripping(self):
        item = strict_json_schema(_Answer)["properties"]["items"]["items"]

        self.assertEqual(item["properties"]["title"], {"type": "string"})
