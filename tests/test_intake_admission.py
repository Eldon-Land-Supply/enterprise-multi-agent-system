import hashlib
import hmac
import json

from webhook_gateway.github_admission import GitHubAdmissionPolicy
from webhook_gateway.idempotency import MemoryIdempotencyStore
from webhook_gateway.intake import WebhookIntake
from webhook_gateway.queueing import MemoryEventPublisher
from webhook_gateway.quota import MemoryDailyQuota


def signed(body, delivery_id, event_type):
    secret = "secret"
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return secret, {
        "X-GitHub-Delivery": delivery_id,
        "X-GitHub-Event": event_type,
        "X-Hub-Signature-256": f"sha256={signature}",
    }


def test_public_comment_is_acknowledged_but_not_enqueued_or_charged():
    publisher = MemoryEventPublisher()
    quota = MemoryDailyQuota()
    intake = WebhookIntake(
        MemoryIdempotencyStore(),
        publisher,
        github_policy=GitHubAdmissionPolicy({"123"}, {"issue_comment"}),
        github_quota=quota,
        github_daily_limit=1,
    )
    body = json.dumps(
        {
            "action": "created",
            "repository": {"id": 123, "full_name": "owner/repo"},
            "comment": {"author_association": "NONE"},
        }
    ).encode()
    secret, headers = signed(body, "delivery-public", "issue_comment")

    result = intake.github(body, headers, secret)

    assert result.ignored == 1
    assert publisher.events == []
    assert quota.allow("123", "another-event", 1)


def test_repository_quota_is_idempotent_and_fails_closed_at_limit():
    publisher = MemoryEventPublisher()
    intake = WebhookIntake(
        MemoryIdempotencyStore(),
        publisher,
        github_policy=GitHubAdmissionPolicy({"123"}, {"push"}),
        github_quota=MemoryDailyQuota(),
        github_daily_limit=1,
    )
    body = json.dumps({"repository": {"id": 123, "full_name": "owner/repo"}}).encode()
    secret, first_headers = signed(body, "delivery-1", "push")
    _, second_headers = signed(body, "delivery-2", "push")

    assert intake.github(body, first_headers, secret).accepted == 1
    assert intake.github(body, first_headers, secret).duplicates == 1
    limited = intake.github(body, second_headers, secret)

    assert limited.rate_limited == 1
    assert len(publisher.events) == 1
