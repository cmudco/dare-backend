"""Reading the JSON object a model reply was asked to return."""

import json


def find_json_object(text, required_key):
    """The first complete JSON object in ``text`` that has ``required_key``.

    Tolerates prose and fences around it and trailing text after it: models
    sometimes echo the contract template after the real object, which breaks
    whole-string parsing.
    """
    decoder = json.JSONDecoder()
    index = (text or "").find("{")
    while index != -1:
        try:
            obj, _ = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict) and required_key in obj:
            return obj
        index = text.find("{", index + 1)
    return None
