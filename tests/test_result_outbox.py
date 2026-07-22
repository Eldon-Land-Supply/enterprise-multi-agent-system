from types import SimpleNamespace

import function_app
from webhook_gateway.events import EventEnvelope
from webhook_gateway.idempotency import MemoryIdempotencyStore
from webhook_gateway.worker import ProcessingResult


class FailOncePublisher:
    def __init__(self):
        self.calls = 0
        self.events = []

    def publish(self, event):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("ambiguous result send")
        self.events.append(event)


class Processor:
    def __init__(self):
        self.process_calls = 0
        self.acknowledged = []
        self.abandoned = []

    def process(self, event):
        self.process_calls += 1
        return ProcessingResult(
            event.id,
            event.correlation_id,
            "completed",
            {"value": self.process_calls},
        )

    def acknowledge(self, result):
        self.acknowledged.append(result)

    def abandon(self, result):
        self.abandoned.append(result)


def test_result_send_failure_reuses_stored_winner_without_reprocessing(monkeypatch):
    event = EventEnvelope("delivery-1", "github", "push", {}, "delivery-1")
    message = SimpleNamespace(get_body=lambda: event.to_json().encode())
    store = MemoryIdempotencyStore(existing_payload_policy="first_wins")
    publisher = FailOncePublisher()
    processor = Processor()
    monkeypatch.setattr(function_app, "build_payload_store", lambda: None)
    monkeypatch.setattr(function_app, "build_result_store", lambda: store)
    monkeypatch.setattr(function_app, "build_result_publisher", lambda: publisher)
    monkeypatch.setattr(function_app, "_processor", lambda: processor)

    function_app.process_event(message)
    assert processor.process_calls == 1
    assert processor.acknowledged == []
    assert len(store.pending()) == 1

    function_app.process_event(message)

    assert processor.process_calls == 1
    assert len(processor.acknowledged) == 1
    assert store.pending() == []
    assert len(publisher.events) == 1
    assert publisher.events[0].data["output"] == {"value": 1}
    assert "checkpoint" not in publisher.events[0].data


def test_private_onedrive_checkpoint_is_acknowledged_but_not_published(monkeypatch):
    result = ProcessingResult(
        "signal-1",
        "subscription-1",
        "completed",
        {"changes": [{"id": "item-1"}]},
        checkpoint={"delta_link": "opaque", "lease_owner": "owner"},
    )
    private_event = EventEnvelope(
        "onedrive:signal-1",
        "result-outbox",
        "processing.completed",
        result.to_private_record(),
        "subscription-1",
    )
    store = MemoryIdempotencyStore(existing_payload_policy="first_wins")
    staged = store.stage(private_event)
    publisher = SimpleNamespace(
        events=[], publish=lambda event: publisher.events.append(event)
    )
    processor = Processor()
    monkeypatch.setattr(function_app, "build_payload_store", lambda: None)
    monkeypatch.setattr(function_app, "build_result_store", lambda: store)
    monkeypatch.setattr(function_app, "build_result_publisher", lambda: publisher)
    monkeypatch.setattr(function_app, "_processor", lambda: processor)

    function_app._dispatch_result_record(staged.event)

    assert processor.acknowledged[0].checkpoint["delta_link"] == "opaque"
    assert "checkpoint" not in publisher.events[0].data
    assert store.pending() == []
