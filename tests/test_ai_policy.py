import json
from types import SimpleNamespace

from webhook_gateway.analysis import OpenAIAnalysisProvider, sanitize_for_model
from webhook_gateway.events import EventEnvelope


def test_openai_analysis_disables_storage_and_server_forces_approval():
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            output_text=json.dumps(
                {
                    "summary": "Routine update",
                    "category": "operations",
                    "urgency": "low",
                    "recommended_action": "Send an external message",
                    "requires_human_approval": False,
                }
            )
        )

    provider = OpenAIAnalysisProvider(
        "approved-model",
        client=SimpleNamespace(responses=SimpleNamespace(create=create)),
    )
    result = provider.analyze(EventEnvelope("evt-1", "github", "push", {}, "evt-1"))

    assert captured["store"] is False
    assert result.requires_human_approval is True


def test_sanitizer_redacts_connection_strings_sas_urls_and_jwts():
    value = (
        "AccountKey=abcdefghijklmnopqrstuvwxyz; "
        "https://blob.example/x?sv=1&sig=very-secret-signature; "
        "eyJheaderpayload.eyJbodypayload.eyJsignaturevalue"
    )

    sanitized = sanitize_for_model(value)

    assert "AccountKey" not in sanitized
    assert "sig=" not in sanitized
    assert "eyJheaderpayload" not in sanitized
