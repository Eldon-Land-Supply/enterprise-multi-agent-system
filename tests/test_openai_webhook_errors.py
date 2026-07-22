import azure.functions as func
from openai import OpenAI

import function_app


def test_missing_openai_webhook_headers_return_400(monkeypatch):
    monkeypatch.setattr(
        function_app,
        "_openai_webhook_client",
        lambda: OpenAI(api_key="test-key", webhook_secret="test-webhook-secret"),
    )
    request = func.HttpRequest(
        method="POST",
        url="https://example.com/api/webhooks/openai",
        headers={},
        params={},
        route_params={},
        body=b'{"id":"evt_1","type":"response.completed","data":{}}',
    )

    response = function_app.openai_webhook(request)

    assert response.status_code == 400
    assert b"invalid_signature" in response.get_body()
