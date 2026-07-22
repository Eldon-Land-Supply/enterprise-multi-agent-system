from webhook_gateway.analysis import event_prompt, sanitize_for_model
from webhook_gateway.events import EventEnvelope


def test_sanitizer_redacts_sensitive_keys_and_token_patterns():
    value = {
        "authorization": "Bearer abcdefghijklmnopqrstuvwxyz",
        "nested": {
            "clientState": "opaque-secret-state",
            "body": "token sk-example_abcdefghijklmnop appeared",
        },
        "safe": "keep me",
    }

    sanitized = sanitize_for_model(value)

    assert sanitized["authorization"] == "[redacted]"
    assert sanitized["nested"]["clientState"] == "[redacted]"
    assert "sk-example" not in sanitized["nested"]["body"]
    assert sanitized["safe"] == "keep me"


def test_event_prompt_does_not_include_download_urls_or_credentials():
    event = EventEnvelope(
        id="evt-1",
        source="onedrive",
        type="drive.updated",
        data={
            "@microsoft.graph.downloadUrl": "https://temporary.example/secret",
            "name": "safe.docx",
        },
        metadata={"access_token": "top-secret"},
        correlation_id="evt-1",
    )

    prompt = event_prompt(event)

    assert "temporary.example" not in prompt
    assert "top-secret" not in prompt
    assert "safe.docx" in prompt
