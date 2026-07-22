import base64
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import anthropic
import azure.functions as func
from standardwebhooks.webhooks import Webhook

import function_app
from webhook_gateway.idempotency import MemoryIdempotencyStore
from webhook_gateway.intake import WebhookIntake
from webhook_gateway.queueing import MemoryEventPublisher


def test_anthropic_endpoint_accepts_real_sdk_signed_managed_agent_event(monkeypatch):
    key = "whsec_" + base64.b64encode(b"a" * 32).decode()
    body = json.dumps(
        {
            "type": "event",
            "id": "event_01ABC",
            "created_at": "2026-03-18T14:05:22Z",
            "data": {
                "type": "session.status_idled",
                "id": "sesn_01XYZ",
                "organization_id": "org-1",
                "workspace_id": "workspace-1",
            },
        },
        separators=(",", ":"),
    )
    timestamp = datetime.now(timezone.utc)
    headers = {
        "webhook-id": "msg_1",
        "webhook-timestamp": str(int(timestamp.timestamp())),
        "webhook-signature": Webhook(key).sign("msg_1", timestamp, body),
    }
    publisher = MemoryEventPublisher()
    intake = WebhookIntake(MemoryIdempotencyStore(), publisher)
    monkeypatch.setattr(
        function_app,
        "get_settings",
        lambda: SimpleNamespace(anthropic_webhook_signing_key=key),
    )
    monkeypatch.setattr(
        function_app,
        "_anthropic_webhook_client",
        lambda: anthropic.Anthropic(api_key="test-api-key"),
    )
    monkeypatch.setattr(function_app, "_intake", lambda: intake)
    request = func.HttpRequest(
        method="POST",
        url="https://example.com/api/webhooks/anthropic",
        headers=headers,
        params={},
        route_params={},
        body=body.encode(),
    )

    response = function_app.anthropic_webhook(request)

    assert response.status_code == 200
    assert publisher.events[0].type == "session.status_idled"
