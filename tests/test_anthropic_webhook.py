from webhook_gateway.idempotency import MemoryIdempotencyStore
from webhook_gateway.intake import WebhookIntake
from webhook_gateway.queueing import MemoryEventPublisher


def test_verified_anthropic_agent_event_is_normalized_and_deduplicated():
    publisher = MemoryEventPublisher()
    intake = WebhookIntake(MemoryIdempotencyStore(), publisher)
    payload = {
        "type": "event",
        "id": "event_01ABC",
        "created_at": "2026-03-18T14:05:22Z",
        "data": {
            "type": "session.status_idled",
            "id": "sesn_01XYZ",
            "organization_id": "org-1",
            "workspace_id": "workspace-1",
        },
    }

    first = intake.verified_anthropic(payload)
    second = intake.verified_anthropic(payload)

    assert first.accepted == 1
    assert second.duplicates == 1
    assert publisher.events[0].source == "anthropic"
    assert publisher.events[0].type == "session.status_idled"
    assert publisher.events[0].subject == "sesn_01XYZ"
