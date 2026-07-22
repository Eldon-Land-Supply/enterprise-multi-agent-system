import azure.functions as func
import pytest

import function_app
from src.webhook_gateway.errors import WebhookError


def request(*, body=b"", params=None):
    return func.HttpRequest(
        method="POST",
        url="https://example.com/api/webhooks/onedrive",
        headers={},
        params=params or {},
        route_params={},
        body=body,
    )


def test_onedrive_subscription_validation_returns_exact_plain_text():
    response = function_app.onedrive_webhook(
        request(params={"validationToken": "opaque token+value"})
    )

    assert response.status_code == 200
    assert response.get_body() == b"opaque token+value"
    assert response.mimetype == "text/plain"


def test_body_limit_rejects_oversized_event(monkeypatch):
    monkeypatch.setenv("MAX_WEBHOOK_BYTES", "2")

    with pytest.raises(WebhookError) as exc_info:
        function_app._body(request(body=b"123"))

    assert exc_info.value.status_code == 413
