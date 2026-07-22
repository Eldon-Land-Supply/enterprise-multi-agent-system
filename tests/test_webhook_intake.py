import hashlib
import hmac
import json
import time

import pytest

from webhook_gateway.errors import WebhookError
from webhook_gateway.idempotency import MemoryIdempotencyStore
from webhook_gateway.intake import WebhookIntake
from webhook_gateway.queueing import MemoryEventPublisher


def make_intake():
    publisher = MemoryEventPublisher()
    return WebhookIntake(MemoryIdempotencyStore(), publisher), publisher


def github_headers(body: bytes, delivery_id: str = "delivery-1"):
    secret = "github-secret"
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return secret, {
        "X-Hub-Signature-256": signature,
        "X-GitHub-Delivery": delivery_id,
        "X-GitHub-Event": "pull_request",
    }


def test_github_is_verified_normalized_and_deduplicated():
    intake, publisher = make_intake()
    body = json.dumps(
        {"action": "opened", "repository": {"full_name": "owner/repo"}}
    ).encode()
    secret, headers = github_headers(body)

    first = intake.github(body, headers, secret)
    second = intake.github(body, headers, secret)

    assert first.accepted == 1
    assert second.duplicates == 1
    assert len(publisher.events) == 1
    event = publisher.events[0]
    assert event.id == "delivery-1"
    assert event.source == "github"
    assert event.type == "pull_request"
    assert event.subject == "owner/repo"


def test_github_accepts_previous_secret_during_rotation():
    intake, publisher = make_intake()
    body = b'{"repository":{"full_name":"owner/repo"}}'
    old_secret, headers = github_headers(body)

    result = intake.github(
        body,
        headers,
        "new-secret",
        previous_secret=old_secret,
    )

    assert result.accepted == 1
    assert len(publisher.events) == 1


def test_github_rejects_missing_delivery_headers_before_enqueue():
    intake, publisher = make_intake()

    with pytest.raises(WebhookError) as exc_info:
        intake.github(b"{}", {}, "secret")

    assert exc_info.value.status_code == 400
    assert publisher.events == []


def test_generic_intake_requires_fresh_hmac_and_object_data():
    intake, publisher = make_intake()
    timestamp = str(int(time.time()))
    body = json.dumps(
        {"id": "evt-1", "type": "lead.created", "data": {"lead": 42}}
    ).encode()
    digest = hmac.new(
        b"shared", timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()

    result = intake.generic(
        body,
        {
            "X-Webhook-Timestamp": timestamp,
            "X-Webhook-Signature": f"sha256={digest}",
        },
        "shared",
    )

    assert result.accepted == 1
    assert publisher.events[0].type == "lead.created"


def test_openai_uses_webhook_header_as_idempotency_key():
    intake, publisher = make_intake()
    payload = {
        "id": "event-object-id",
        "type": "response.completed",
        "data": {"id": "resp_123"},
    }

    first = intake.verified_openai(payload, delivery_id="webhook-delivery-id")
    second = intake.verified_openai(payload, delivery_id="webhook-delivery-id")

    assert first.accepted == 1
    assert second.duplicates == 1
    assert publisher.events[0].id == "webhook-delivery-id"
    assert publisher.events[0].subject == "resp_123"
    assert publisher.events[0].metadata["openai_event_id"] == "event-object-id"


def test_onedrive_batch_validates_state_subscription_and_resource():
    intake, publisher = make_intake()
    body = json.dumps(
        {
            "value": [
                {
                    "subscriptionId": "sub-1",
                    "clientState": "opaque-state",
                    "changeType": "updated",
                    "resource": "drives/drive-1/root",
                    "tenantId": "tenant-1",
                    "sequenceNumber": "1",
                },
                {
                    "subscriptionId": "sub-1",
                    "clientState": "opaque-state",
                    "changeType": "updated",
                    "resource": "drives/drive-1/root",
                    "tenantId": "tenant-1",
                    "sequenceNumber": "2",
                },
            ]
        }
    ).encode()

    result = intake.onedrive(
        body,
        "opaque-state",
        expected_subscription_id="sub-1",
        expected_resource="drives/drive-1/root",
        expected_tenant_id="tenant-1",
    )

    assert result.accepted == 1
    assert len(publisher.events) == 1
    assert publisher.events[0].id.startswith("sub-1:")
    assert publisher.events[0].data["notificationCount"] == 2


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("clientState", "wrong", "invalid_client_state"),
        ("subscriptionId", "wrong", "invalid_subscription"),
        ("resource", "drives/wrong/root", "invalid_resource"),
    ],
)
def test_onedrive_rejects_mismatched_notification_identity(field, value, code):
    intake, _ = make_intake()
    notification = {
        "subscriptionId": "sub-1",
        "clientState": "state",
        "resource": "drives/drive-1/root",
    }
    notification[field] = value

    with pytest.raises(WebhookError) as exc_info:
        intake.onedrive(
            json.dumps({"value": [notification]}).encode(),
            "state",
            expected_subscription_id="sub-1",
            expected_resource="drives/drive-1/root",
        )

    assert exc_info.value.code == code


def test_onedrive_lifecycle_event_is_routed_separately():
    intake, publisher = make_intake()
    body = json.dumps(
        {
            "value": [
                {
                    "subscriptionId": "sub-1",
                    "clientState": "state",
                    "tenantId": "tenant-1",
                    "lifecycleEvent": "reauthorizationRequired",
                }
            ]
        }
    ).encode()

    intake.onedrive(
        body,
        "state",
        expected_subscription_id="sub-1",
        expected_resource="drives/drive-1/root",
        expected_tenant_id="tenant-1",
    )

    assert publisher.events[0].type == "drive.lifecycle.reauthorizationRequired"
