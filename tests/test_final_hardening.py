import io
import json
import urllib.error
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import function_app
from webhook_gateway.idempotency import MemoryIdempotencyStore
from webhook_gateway.intake import WebhookIntake
from webhook_gateway.onedrive import GraphClient, GraphRequestError
from webhook_gateway.onedrive_state import (
    MemoryOneDriveSubscriptionStore,
    OneDriveSubscriptionState,
)
from webhook_gateway.onedrive_sync import MemoryDeltaCursorStore
from webhook_gateway.queueing import MemoryEventPublisher


class Tokens:
    def token(self):
        return "test-token"


def _settings():
    return SimpleNamespace(
        onedrive_drive_id="drive-1",
        onedrive_tenant_id="tenant-1",
        onedrive_client_state="opaque-state",
        public_base_url="https://gateway.example.com/api",
        github_webhook_secret="github-secret",
        github_allowed_repository_ids=frozenset({"1116614709"}),
        github_allowed_events=frozenset({"push"}),
        openai_webhook_secret="openai-secret",
        anthropic_webhook_signing_key="anthropic-secret",
        service_bus_connection=None,
        service_bus_namespace="gateway.servicebus.windows.net",
        ai_provider="both",
        openai_api_key="openai-key",
        openai_model="gpt-test",
        anthropic_api_key="anthropic-key",
        anthropic_model="claude-test",
    )


def _state(*, notification_url="https://gateway.example.com/api/webhooks/onedrive"):
    return OneDriveSubscriptionState(
        subscription_id="subscription-old",
        drive_id="drive-1",
        expiration=datetime.now(timezone.utc) + timedelta(days=20),
        resource="drives/drive-1/root",
        notification_url=notification_url,
    )


def test_graph_request_error_preserves_non_delta_status():
    def opener(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            404,
            "not found",
            {},
            io.BytesIO(b'{"error":"gone"}'),
        )

    client = GraphClient(Tokens(), opener=opener)

    with pytest.raises(GraphRequestError) as exc_info:
        client.get_subscription("removed")

    assert exc_info.value.status == 404
    assert "gone" in exc_info.value.details


def test_direct_onedrive_intake_needs_no_outbox_store():
    publisher = MemoryEventPublisher()
    intake = WebhookIntake(None, publisher, direct_sources=frozenset({"onedrive"}))
    notification = {
        "subscriptionId": "sub-1",
        "clientState": "opaque-state",
        "resource": "drives/drive-1/root",
        "tenantId": "tenant-1",
        "changeType": "updated",
    }

    result = intake.onedrive(
        json.dumps({"value": [notification]}).encode(),
        "opaque-state",
        expected_subscription_id="sub-1",
        expected_resource="drives/drive-1/root",
        expected_tenant_id="tenant-1",
    )

    assert result.accepted == 1
    assert len(publisher.events) == 1


def test_bootstrap_deletes_stale_callback_before_replacement(monkeypatch):
    settings = _settings()
    states = MemoryOneDriveSubscriptionStore()
    states.save(
        _state(notification_url="https://old.example.com/api/webhooks/onedrive")
    )
    cursors = MemoryDeltaCursorStore()
    cursors.set("drive-1", "https://graph.microsoft.com/v1.0/delta-seed")
    stale = {
        "id": "subscription-old",
        "resource": "/drives/drive-1/root",
        "changeType": "updated",
        "notificationUrl": "https://old.example.com/api/webhooks/onedrive",
        "lifecycleNotificationUrl": "https://old.example.com/api/webhooks/onedrive",
        "expirationDateTime": (
            datetime.now(timezone.utc) + timedelta(days=20)
        ).isoformat(),
    }

    class Graph:
        def __init__(self):
            self.deleted = []

        def get_subscription(self, subscription_id):
            return stale

        def list_subscriptions(self):
            return [stale]

        def delete_subscription(self, subscription_id):
            self.deleted.append(subscription_id)

        def create_drive_subscription(self, **kwargs):
            assert self.deleted == ["subscription-old"]
            return {
                "id": "subscription-new",
                "resource": "/drives/drive-1/root",
                "changeType": "updated",
                "notificationUrl": kwargs["notification_url"],
                "lifecycleNotificationUrl": kwargs["lifecycle_notification_url"],
                "expirationDateTime": (
                    datetime.now(timezone.utc) + timedelta(days=28)
                ).isoformat(),
            }

    graph = Graph()
    monkeypatch.setattr(function_app, "_graph", lambda: graph)
    monkeypatch.setattr(function_app, "build_onedrive_cursor_store", lambda: cursors)
    monkeypatch.setattr(
        function_app, "build_onedrive_subscription_store", lambda: states
    )

    state, reused = function_app._ensure_onedrive_subscription(settings)

    assert reused is False
    assert graph.deleted == ["subscription-old"]
    assert state.subscription_id == "subscription-new"


def test_maintenance_reconciles_when_subscription_lookup_fails(monkeypatch):
    settings = _settings()
    states = MemoryOneDriveSubscriptionStore()
    states.save(_state())
    cursors = MemoryDeltaCursorStore()
    cursors.set("drive-1", "https://graph.microsoft.com/v1.0/delta-seed")
    store = MemoryIdempotencyStore()
    publisher = MemoryEventPublisher()

    class Graph:
        def get_subscription(self, subscription_id):
            raise RuntimeError("Graph temporarily unavailable")

    monkeypatch.setattr(function_app, "get_settings", lambda: settings)
    monkeypatch.setattr(function_app, "_graph", lambda: Graph())
    monkeypatch.setattr(function_app, "build_onedrive_cursor_store", lambda: cursors)
    monkeypatch.setattr(
        function_app, "build_onedrive_subscription_store", lambda: states
    )
    monkeypatch.setattr(function_app, "build_store", lambda: store)
    monkeypatch.setattr(function_app, "build_intake_publisher", lambda: publisher)

    function_app.maintain_onedrive_subscription(None)

    assert len(publisher.events) == 1
    assert publisher.events[0].data["reason"] == "scheduled_reconciliation"


def test_health_rejects_expired_or_stale_onedrive_state(monkeypatch):
    settings = _settings()
    states = MemoryOneDriveSubscriptionStore()
    states.save(
        OneDriveSubscriptionState(
            subscription_id="expired",
            drive_id="drive-1",
            expiration=datetime.now(timezone.utc) - timedelta(minutes=1),
            resource="drives/drive-1/root",
            notification_url="https://old.example.com/api/webhooks/onedrive",
        )
    )
    monkeypatch.setattr(function_app, "get_settings", lambda: settings)
    monkeypatch.setattr(
        function_app, "build_onedrive_subscription_store", lambda: states
    )

    response = function_app.health(None)
    body = json.loads(response.get_body())

    assert response.status_code == 503
    assert body["onedrive_webhook"] is False
