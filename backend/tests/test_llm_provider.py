from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.agents.graph import GeminiFailoverLLM, _build_llm
from app.agents.llm_utils import response_text


def _settings(provider: str, gemini_api_key: str = "test-key"):
    return SimpleNamespace(
        llm_provider=provider,
        gemini_api_key=gemini_api_key,
        gemini_model="gemini-3-flash-preview",
        gemini_fallback_model="gemini-3.5-flash",
        openai_api_key="openai-key",
        openai_model="gpt-4o",
        ollama_base_url="http://localhost:11434",
        ollama_model="llama3.1:8b",
    )


class GeminiProviderTests(unittest.TestCase):
    @patch("app.agents.graph.ChatGoogleGenerativeAI")
    def test_gemini_is_constructed_with_the_configured_key_and_model(self, mock_chat):
        llm = _build_llm(_settings("gemini"))

        self.assertIsInstance(llm, GeminiFailoverLLM)
        self.assertEqual(mock_chat.call_count, 2)
        self.assertEqual(mock_chat.call_args_list[0].kwargs["model"], "gemini-3-flash-preview")
        self.assertEqual(mock_chat.call_args_list[1].kwargs["model"], "gemini-3.5-flash")
        for call in mock_chat.call_args_list:
            self.assertEqual(call.kwargs["api_key"], "test-key")
            self.assertEqual(call.kwargs["temperature"], 0)
            self.assertEqual(call.kwargs["max_retries"], 0)

    def test_gemini_requires_a_key(self):
        with self.assertRaisesRegex(ValueError, "GEMINI_API_KEY"):
            _build_llm(_settings("gemini", gemini_api_key=""))

    def test_unknown_provider_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported LLM_PROVIDER"):
            _build_llm(_settings("unsupported"))

    def test_response_text_handles_gemini_content_blocks(self):
        response = SimpleNamespace(content=[
            {"type": "text", "text": "SELECT 1"},
            {"type": "text", "text": "FROM invoices"},
        ])

        self.assertEqual(response_text(response), "SELECT 1\nFROM invoices")

    def test_temporary_provider_failure_uses_the_fallback(self):
        primary = unittest.mock.Mock()
        fallback = unittest.mock.Mock()
        primary.invoke.side_effect = RuntimeError("503 UNAVAILABLE")
        fallback.invoke.return_value = "fallback response"

        result = GeminiFailoverLLM(primary, fallback).invoke("test prompt")

        self.assertEqual(result, "fallback response")
        fallback.invoke.assert_called_once_with("test prompt")
