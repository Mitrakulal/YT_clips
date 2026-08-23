import json
import unittest
from unittest.mock import patch

from shorts_generator.local import llm


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class LocalOllamaTests(unittest.TestCase):
    def test_native_ollama_request_disables_thinking_and_forces_json(self):
        seen = {}

        def fake_urlopen(request, timeout):
            seen["url"] = request.full_url
            seen["body"] = json.loads(request.data.decode("utf-8"))
            seen["timeout"] = timeout
            return _FakeResponse({"message": {"content": '{"highlights": []}'}, "done_reason": "stop"})

        with patch("shorts_generator.local.llm.OPENAI_BASE_URL", "http://localhost:11434/v1"), patch(
            "shorts_generator.local.llm.urlopen", fake_urlopen
        ):
            result = llm.call_ollama_llm("choose candidates")

        self.assertEqual(result, '{"highlights": []}')
        self.assertEqual(seen["url"], "http://localhost:11434/api/chat")
        self.assertFalse(seen["body"]["think"])
        self.assertEqual(seen["body"]["format"], "json")

    def test_native_ollama_empty_content_reports_actionable_error(self):
        with patch("shorts_generator.local.llm.urlopen", lambda *_args, **_kwargs: _FakeResponse({"message": {"content": ""}, "done_reason": "length"})):
            with self.assertRaisesRegex(RuntimeError, "returned no ranking content"):
                llm.call_ollama_llm("choose candidates")
