import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import function_app
from webhook_gateway.onedrive_state import MemoryOneDriveSubscriptionStore
from webhook_gateway.onedrive_sync import MemoryDeltaCursorStore


class Graph:
    def __init__(self):
        self.delta_calls = 0
        self.create_calls = 0
        self.subscriptions = {}

    def list_delta(self, drive_id, latest_only=False):
        self.delta_calls += 1
        assert latest_only
        return [], "https://graph.microsoft.com/delta-seed"

    def list_subscriptions(self):
        return list(self.subscriptions.values())

    def create_drive_subscription(self, **kwargs):
        self.create_calls += 1
        value = {
            "id": "subscription-1",
            "resource": f"/drives/{kwargs['drive_id']}/root",
            "changeType": "updated",
            "notificationUrl": kwargs["notification_url"],
            "lifecycleNotificationUrl": kwargs["lifecycle_notification_url"],
            "expirationDateTime": (
                datetime.now(timezone.utc) + timedelta(days=28)
            ).isoformat(),
        }
        self.subscriptions[value["id"]] = value
        return value

    def get_subscription(self, subscription_id):
        return self.subscriptions[subscription_id]

    def renew_subscription(self, subscription_id, lifetime_days):
        raise AssertionError("fresh subscription should not renew")


def test_bootstrap_seeds_and_reuses_subscription_under_runtime_identity(monkeypatch):
    graph = Graph()
    cursors = MemoryDeltaCursorStore()
    states = MemoryOneDriveSubscriptionStore()
    settings = SimpleNamespace(
        onedrive_drive_id="drive-1",
        onedrive_tenant_id="tenant-1",
        onedrive_client_state="opaque-state",
        public_base_url="https://gateway.example.com/api",
    )
    monkeypatch.setattr(function_app, "get_settings", lambda: settings)
    monkeypatch.setattr(function_app, "_graph", lambda: graph)
    monkeypatch.setattr(function_app, "build_onedrive_cursor_store", lambda: cursors)
    monkeypatch.setattr(
        function_app, "build_onedrive_subscription_store", lambda: states
    )
    request = SimpleNamespace()

    first = function_app.bootstrap_onedrive(request)
    second = function_app.bootstrap_onedrive(request)

    assert first.status_code == 200
    assert second.status_code == 200
    assert json.loads(second.get_body())["reused"] is True
    assert cursors.get("drive-1") == "https://graph.microsoft.com/delta-seed"
    assert graph.delta_calls == 1
    assert graph.create_calls == 1
    assert states.get("drive-1").subscription_id == "subscription-1"
