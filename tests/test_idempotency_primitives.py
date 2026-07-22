import sys
from types import ModuleType, SimpleNamespace

import pytest

from webhook_gateway.events import EventEnvelope
from webhook_gateway.idempotency import (
    AzureTableIdempotencyStore,
    MemoryIdempotencyStore,
    SqliteIdempotencyStore,
)
from webhook_gateway.payloads import POINTER_KEY, AzureBlobEventPayloadStore


def _event(value: int) -> EventEnvelope:
    return EventEnvelope(
        id="event-1",
        source="github",
        type="push",
        data={"value": value},
        correlation_id="correlation-1",
        received_at="2026-07-22T00:00:00+00:00",
    )


def test_memory_get_and_first_wins_return_original_record():
    store = MemoryIdempotencyStore(existing_payload_policy="first_wins")
    first = _event(1)

    assert store.get(first.source, first.id) is None
    assert store.stage(first).created is True

    duplicate = store.stage(_event(2))

    assert duplicate.created is False
    assert duplicate.event == first
    assert store.get(first.source, first.id) == duplicate

    store.mark_sent(first)

    recovered = store.get(first.source, first.id)
    assert recovered is not None
    assert recovered.event == first
    assert recovered.state == "sent"


def test_memory_rejects_changed_payload_by_default():
    store = MemoryIdempotencyStore()
    store.stage(_event(1))

    with pytest.raises(RuntimeError, match="different payload"):
        store.stage(_event(2))


def test_sqlite_get_and_first_wins_survive_store_reopen(tmp_path):
    path = str(tmp_path / "outbox.sqlite3")
    first = _event(1)
    SqliteIdempotencyStore(path).stage(first)
    recovered_store = SqliteIdempotencyStore(path, existing_payload_policy="first_wins")

    duplicate = recovered_store.stage(_event(2))

    assert duplicate.created is False
    assert duplicate.event == first
    assert recovered_store.get(first.source, first.id) == duplicate
    assert recovered_store.get("github", "missing") is None


def test_existing_payload_policy_is_validated():
    with pytest.raises(ValueError, match="existing_payload_policy"):
        MemoryIdempotencyStore(existing_payload_policy="last_wins")  # type: ignore[arg-type]


def _install_fake_azure_tables(monkeypatch):
    azure = ModuleType("azure")
    azure_core = ModuleType("azure.core")
    azure_core_exceptions = ModuleType("azure.core.exceptions")
    azure_data = ModuleType("azure.data")
    azure_tables = ModuleType("azure.data.tables")

    class ResourceExistsError(Exception):
        pass

    class ResourceNotFoundError(Exception):
        pass

    class FakeTable:
        def __init__(self):
            self.entities = {}

        def create_entity(self, entity):
            key = (entity["PartitionKey"], entity["RowKey"])
            if key in self.entities:
                raise ResourceExistsError
            self.entities[key] = dict(entity)

        def get_entity(self, partition_key, row_key):
            try:
                return self.entities[(partition_key, row_key)]
            except KeyError as exc:
                raise ResourceNotFoundError from exc

        def update_entity(self, entity, mode):
            key = (entity["PartitionKey"], entity["RowKey"])
            self.entities[key].update(entity)

        def query_entities(self, query_filter, select):
            return [
                entity
                for entity in self.entities.values()
                if entity.get("State") == "pending"
            ]

    table = FakeTable()

    class FakeTableServiceClient:
        @classmethod
        def from_connection_string(cls, connection_string):
            return cls()

        def create_table_if_not_exists(self, table_name):
            return table

    azure_core_exceptions.ResourceExistsError = ResourceExistsError
    azure_core_exceptions.ResourceNotFoundError = ResourceNotFoundError
    azure_tables.TableServiceClient = FakeTableServiceClient
    azure_tables.UpdateMode = SimpleNamespace(MERGE="merge")
    monkeypatch.setitem(sys.modules, "azure", azure)
    monkeypatch.setitem(sys.modules, "azure.core", azure_core)
    monkeypatch.setitem(sys.modules, "azure.core.exceptions", azure_core_exceptions)
    monkeypatch.setitem(sys.modules, "azure.data", azure_data)
    monkeypatch.setitem(sys.modules, "azure.data.tables", azure_tables)
    return table


class _PassthroughPayloads:
    def prepare(self, event):
        return event

    def resolve(self, event):
        return event


def test_azure_table_get_and_first_wins_return_table_winner(monkeypatch):
    _install_fake_azure_tables(monkeypatch)
    store = AzureTableIdempotencyStore(
        "UseDevelopmentStorage=true",
        "outbox",
        _PassthroughPayloads(),
        existing_payload_policy="first_wins",
    )
    first = _event(1)

    assert store.get(first.source, first.id) is None
    assert store.stage(first).created is True

    duplicate = store.stage(_event(2))

    assert duplicate.created is False
    assert duplicate.event == first
    assert store.get(first.source, first.id) == duplicate


def _install_fake_azure_blobs(monkeypatch):
    azure = ModuleType("azure")
    azure_core = ModuleType("azure.core")
    azure_core_exceptions = ModuleType("azure.core.exceptions")
    azure_storage = ModuleType("azure.storage")
    azure_blobs = ModuleType("azure.storage.blob")

    class ResourceExistsError(Exception):
        pass

    class FakeBlob:
        def __init__(self, container, name):
            self.container = container
            self.name = name

        def upload_blob(self, payload, overwrite, metadata):
            if self.name in self.container.blobs:
                raise ResourceExistsError
            self.container.blobs[self.name] = (payload, dict(metadata))

        def get_blob_properties(self):
            return SimpleNamespace(metadata=self.container.blobs[self.name][1])

    class FakeContainer:
        def __init__(self):
            self.blobs = {}

        def get_blob_client(self, name):
            return FakeBlob(self, name)

    container = FakeContainer()

    class FakeBlobServiceClient:
        @classmethod
        def from_connection_string(cls, connection_string):
            return cls()

        def get_container_client(self, container_name):
            return container

    azure_core_exceptions.ResourceExistsError = ResourceExistsError
    azure_blobs.BlobServiceClient = FakeBlobServiceClient
    monkeypatch.setitem(sys.modules, "azure", azure)
    monkeypatch.setitem(sys.modules, "azure.core", azure_core)
    monkeypatch.setitem(sys.modules, "azure.core.exceptions", azure_core_exceptions)
    monkeypatch.setitem(sys.modules, "azure.storage", azure_storage)
    monkeypatch.setitem(sys.modules, "azure.storage.blob", azure_blobs)
    return container


def test_blob_candidates_are_content_addressed_beneath_identity_path(monkeypatch):
    container = _install_fake_azure_blobs(monkeypatch)
    store = AzureBlobEventPayloadStore("UseDevelopmentStorage=true", "webhook-payloads")

    first = store.prepare(_event(1))
    second = store.prepare(_event(2))

    first_name = first.data[POINTER_KEY]["name"]
    second_name = second.data[POINTER_KEY]["name"]
    assert first_name.rsplit("/", 1)[0] == second_name.rsplit("/", 1)[0]
    assert first_name != second_name
    assert len(container.blobs) == 2
