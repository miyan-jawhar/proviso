"""Health response configuration tests that never expose provider secrets."""

from types import SimpleNamespace
import unittest

from app.routes.health import _deterministic_audit_status, _reasoning_status


class OperatingModeTests(unittest.TestCase):
    def _settings(self, provider: str, gemini_key: str = "", openai_key: str = "", ollama_model: str = "llama3.1:8b"):
        return SimpleNamespace(
            llm_provider=provider,
            gemini_api_key=gemini_key,
            openai_api_key=openai_key,
            ollama_model=ollama_model,
        )

    def test_hosted_provider_requires_a_configured_key_without_returning_it(self):
        status = _reasoning_status(self._settings("gemini", gemini_key="secret-value"))

        self.assertEqual(status["execution"], "hosted")
        self.assertTrue(status["open_ended_reasoning_configured"])
        self.assertNotIn("secret-value", str(status))

    def test_ollama_is_reported_as_local_but_not_service_verified(self):
        status = _reasoning_status(self._settings("ollama"))

        self.assertEqual(status["execution"], "local")
        self.assertTrue(status["open_ended_reasoning_configured"])
        self.assertFalse(status["service_verified"])

    def test_local_fixture_status_is_structured(self):
        status = _deterministic_audit_status()

        self.assertEqual(status["execution"], "local")
        self.assertIn("available", status)
        self.assertIn("ground_truth_manifest_available", status)
