import json
from datetime import datetime

import pytest

from webhook_gateway.onedrive import GraphClient


class Tokens:
    def token(self):
        return "graph-token"


class FakeHttpResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return json.dumps(self.payload).encode()


def test_create_subscription_targets_business_onedrive_root():
    calls = []

    def opener(request, timeout):
        calls.append((request, timeout))
        return FakeHttpResponse({"id": "sub-1"})

    client = GraphClient(Tokens(), opener=opener)
    result = client.create_drive_subscription(
        drive_id="drive-1",
        notification_url="https://example.com/api/webhooks/onedrive",
        lifecycle_notification_url="https://example.com/api/webhooks/onedrive",
        client_state="opaque",
    )

    request, timeout = calls[0]
    body = json.loads(request.data)
    assert result["id"] == "sub-1"
    assert timeout == 30
    assert body["resource"] == "/drives/drive-1/root"
    assert body["changeType"] == "updated"
    assert body["lifecycleNotificationUrl"] == body["notificationUrl"]
    assert datetime.fromisoformat(body["expirationDateTime"].replace("Z", "+00:00"))
    assert request.headers["Authorization"] == "Bearer graph-token"


@pytest.mark.parametrize("days", [0, 30])
def test_create_subscription_rejects_unsafe_lifetime(days):
    client = GraphClient(Tokens())

    with pytest.raises(ValueError):
        client.create_drive_subscription(
            drive_id="drive-1",
            notification_url="https://example.com/hook",
            client_state="state",
            lifetime_days=days,
        )


def test_delta_follows_opaque_next_link_and_returns_final_cursor():
    responses = iter(
        [
            {
                "value": [{"id": "item-1"}],
                "@odata.nextLink": "https://graph.microsoft.com/next-token",
            },
            {
                "value": [{"id": "item-2", "deleted": {"state": "deleted"}}],
                "@odata.deltaLink": "https://graph.microsoft.com/delta-token",
            },
        ]
    )
    urls = []

    def opener(request, timeout):
        urls.append(request.full_url)
        return FakeHttpResponse(next(responses))

    client = GraphClient(Tokens(), opener=opener)
    changes, cursor = client.list_delta("drive-1")

    assert [item["id"] for item in changes] == ["item-1", "item-2"]
    assert urls[1] == "https://graph.microsoft.com/next-token"
    assert cursor == "https://graph.microsoft.com/delta-token"


def test_delta_refuses_external_next_link_before_requesting_a_token():
    class CountingTokens:
        calls = 0

        def token(self):
            self.calls += 1
            return "graph-token"

    tokens = CountingTokens()
    client = GraphClient(tokens)

    with pytest.raises(RuntimeError, match="another origin"):
        client._request_url("GET", "https://attacker.example/steal")

    assert tokens.calls == 0
