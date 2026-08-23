import json

from django.test import SimpleTestCase

from core.services.tool_loop.persistence import (MAX_PERSISTED_RESULT_CHARS,
                                                 serialize_persisted_result)


class ToolLoopPersistenceTests(SimpleTestCase):
    def test_large_result_is_compacted_as_valid_json(self):
        result = {
            "success": True,
            "artifactId": 212,
            "message": "created",
            "config": {"content": "x" * 6000},
        }

        serialized = serialize_persisted_result(result)
        parsed = json.loads(serialized)

        self.assertLessEqual(len(serialized), MAX_PERSISTED_RESULT_CHARS)
        self.assertTrue(parsed["truncated"])
        self.assertTrue(parsed["success"])
        self.assertEqual(parsed["artifactId"], 212)
        self.assertEqual(parsed["message"], "created")
        self.assertEqual(parsed["original_chars"], len(json.dumps(result)))
        self.assertTrue(parsed["content_preview"])

    def test_small_result_is_preserved(self):
        result = {"success": True, "value": "complete"}

        serialized = serialize_persisted_result(result)

        self.assertEqual(json.loads(serialized), result)
