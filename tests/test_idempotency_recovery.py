import hashlib
import hmac
import json

import pytest

from webhook_gateway.idempotency import MemoryIdempotencyStore
from webhook_gateway.intake import WebhookIntake


class FailOncePublisher:
    def __init__(self):
        self.attempts = 0
        self.events = []

    def publish(self, event):
        self.attempts += 1
        if self.attempts == 1:
            raise RuntimeError("queue unavailable")
        self.events.append(event)


def test_failed_enqueue_leaves_pending_outbox_so_retry_can_recover():
    body = json.dumps({"repository": {"full_name": "owner/repo"}}).encode()
    secret = "secret"
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {
        "X-Hub-Signature-256": signature,
        "X-GitHub-Delivery": "delivery-1",
        "X-GitHub-Event": "push",
    }
    publisher = FailOncePublisher()
    intake = WebhookIntake(MemoryIdempotencyStore(), publisher)

    with pytest.raises(RuntimeError, match="queue unavailable"):
        intake.github(body, headers, secret)

    retry = intake.github(body, headers, secret)

    assert retry.accepted == 1
    assert publisher.attempts == 2
    assert len(publisher.events) == 1
