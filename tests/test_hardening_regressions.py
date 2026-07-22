import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest

from webhook_gateway.events import EventEnvelope
from webhook_gateway.idempotency import MemoryIdempotencyStore
from webhook_gateway.intake import WebhookIntake
from webhook_gateway.onedrive_sync import MemoryDeltaCursorStore, OneDriveDeltaProcessor
from webhook_gateway.queueing import MemoryEventPublisher, service_bus_message_id


def _signed_github(body: bytes, delivery_id: str = "delivery-1"):
    secret = "secret"
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return secret, {
        "X-GitHub-Delivery": delivery_id,
        "X-GitHub-Event": "push",
        "X-Hub-Signature-256": f"sha256={digest}",
    }


class AlwaysFailPublisher:
    def publish(self, event):
        raise RuntimeError("queue unavailable")


def test_outbox_keeps_event_pending_when_queue_send_fails():
    body = b'{"repository":{"full_name":"owner/repo"}}'
    secret, headers = _signed_github(body)
    store = MemoryIdempotencyStore()
    intake = WebhookIntake(store, AlwaysFailPublisher())

    with pytest.raises(RuntimeError, match="queue unavailable"):
        intake.github(body, headers, secret)

    pending = store.pending()
    assert len(pending) == 1
    assert pending[0].id == "delivery-1"


def test_outbox_rejects_same_source_and_id_with_changed_payload():
    store = MemoryIdempotencyStore()
    first = EventEnvelope("same", "github", "push", {"a": 1}, "same")
    second = EventEnvelope("same", "github", "push", {"a": 2}, "same")

    store.stage(first)

    with pytest.raises(RuntimeError, match="different payload"):
        store.stage(second)


def test_service_bus_ids_are_namespaced_by_source():
    github = EventEnvelope("same", "github", "push", {}, "same")
    openai = EventEnvelope("same", "openai", "response.completed", {}, "same")

    assert service_bus_message_id(github) != service_bus_message_id(openai)
    assert service_bus_message_id(github) == service_bus_message_id(github)
    assert len(service_bus_message_id(github)) == 64


def test_identical_onedrive_dirty_signals_are_collapsed_into_one_delta_job():
    publisher = MemoryEventPublisher()
    intake = WebhookIntake(MemoryIdempotencyStore(), publisher)
    notification = {
        "subscriptionId": "sub-1",
        "clientState": "state",
        "resource": "drives/drive-1/root",
        "tenantId": "tenant-1",
        "changeType": "updated",
    }
    body = json.dumps({"value": [notification, notification]}).encode()

    result = intake.onedrive(
        body,
        "state",
        expected_subscription_id="sub-1",
        expected_resource="drives/drive-1/root",
        expected_tenant_id="tenant-1",
    )

    assert result.accepted == 1
    assert len(publisher.events) == 1
    assert publisher.events[0].data["notificationCount"] == 2
    assert "clientState" not in publisher.events[0].data


def test_onedrive_abandon_keeps_cursor_and_strips_download_url():
    cursors = MemoryDeltaCursorStore()
    graph = SimpleNamespace(
        list_delta=lambda drive_id, delta_link=None: (
            [
                {
                    "id": "item-1",
                    "name": "safe.docx",
                    "@microsoft.graph.downloadUrl": "https://temp.example/secret",
                }
            ],
            "https://graph.microsoft.com/v1.0/delta-token",
        )
    )
    processor = OneDriveDeltaProcessor("drive-1", graph, cursors)
    event = EventEnvelope(
        "signal-1",
        "onedrive",
        "drive.updated",
        {"subscriptionId": "sub-1"},
        "sub-1",
    )

    output = processor.process(event)
    processor.abandon(output["_checkpoint"])

    assert cursors.get("drive-1") is None
    assert "downloadUrl" not in json.dumps(output)
    assert output["changes"][0]["name"] == "safe.docx"
