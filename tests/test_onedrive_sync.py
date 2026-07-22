from types import SimpleNamespace

from webhook_gateway.events import EventEnvelope
from webhook_gateway.onedrive_sync import MemoryDeltaCursorStore, OneDriveDeltaProcessor


def event(event_type="drive.updated"):
    return EventEnvelope(
        id="sub-1:1",
        source="onedrive",
        type=event_type,
        data={"subscriptionId": "sub-1"},
        correlation_id="sub-1",
    )


def test_delta_processor_collapses_items_and_advances_cursor_after_success():
    cursors = MemoryDeltaCursorStore()
    graph = SimpleNamespace(
        list_delta=lambda drive_id, delta_link=None: (
            [
                {"id": "item-1", "name": "old"},
                {"id": "item-1", "name": "new"},
                {"id": "item-2", "deleted": {"state": "deleted"}},
            ],
            "https://opaque.example/delta-2",
        )
    )
    processor = OneDriveDeltaProcessor("drive-1", graph, cursors)

    result = processor.process(event())

    assert result["change_count"] == 2
    assert result["changes"][0]["name"] == "new"
    assert cursors.get("drive-1") is None

    processor.acknowledge(result["_checkpoint"])

    assert cursors.get("drive-1") == "https://opaque.example/delta-2"


def test_stale_lease_owner_cannot_overwrite_cursor():
    cursors = MemoryDeltaCursorStore()
    assert cursors.acquire("drive-1", "owner-1")
    cursors.release("drive-1", "owner-1")
    assert cursors.acquire("drive-1", "owner-2")

    assert not cursors.commit("drive-1", "delta-stale", "owner-1")
    assert cursors.commit("drive-1", "delta-current", "owner-2")
    assert cursors.commit("drive-1", "delta-current", "owner-2")
    assert cursors.get("drive-1") == "delta-current"


def test_lifecycle_notification_renews_subscription():
    calls = []
    graph = SimpleNamespace(
        renew_subscription=lambda subscription_id, lifetime_days: (
            calls.append((subscription_id, lifetime_days)) or {"id": subscription_id}
        )
    )
    processor = OneDriveDeltaProcessor(
        "drive-1",
        graph,
        MemoryDeltaCursorStore(),
    )

    result = processor.process(event("drive.lifecycle.reauthorizationRequired"))

    assert calls == [("sub-1", 28)]
    assert result["renewed"] is True
