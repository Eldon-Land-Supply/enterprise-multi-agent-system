import hashlib
import sys
from datetime import datetime, timedelta, timezone
from types import ModuleType

import pytest

from webhook_gateway.onedrive_state import (
    AzureTableOneDriveSubscriptionStore,
    MemoryOneDriveSubscriptionStore,
    OneDriveSubscriptionState,
)


def subscription(**overrides):
    values = {
        "subscription_id": "subscription-1",
        "drive_id": "drive/with?unsafe#table-key",
        "expiration": datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc),
        "resource": "/drives/drive-1/root",
        "notification_url": "https://hooks.example.com/api/webhooks/onedrive",
    }
    values.update(overrides)
    return OneDriveSubscriptionState(**values)


def test_memory_store_round_trips_and_replaces_by_drive():
    store = MemoryOneDriveSubscriptionStore()
    original = subscription()
    renewed = subscription(
        subscription_id="subscription-2",
        expiration=original.expiration + timedelta(days=28),
    )

    assert store.get(original.drive_id) is None
    store.save(original)
    store.save(renewed)

    assert store.get(original.drive_id) == renewed


def test_state_normalizes_naive_expiration_to_utc():
    state = subscription(expiration=datetime(2026, 8, 19, 12, 0))

    assert state.expiration == datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


class FakeResourceNotFoundError(Exception):
    pass


class FakeTable:
    def __init__(self):
        self.entities = {}
        self.last_mode = None

    def get_entity(self, partition_key, row_key):
        try:
            return self.entities[(partition_key, row_key)]
        except KeyError as exc:
            raise FakeResourceNotFoundError() from exc

    def upsert_entity(self, entity, mode):
        self.last_mode = mode
        self.entities[(entity["PartitionKey"], entity["RowKey"])] = dict(entity)


def install_fake_azure(monkeypatch):
    table = FakeTable()
    calls = {}

    class FakeUpdateMode:
        REPLACE = "replace"

    class FakeTableServiceClient:
        def __init__(self, *, endpoint, credential):
            calls["identity"] = (endpoint, credential)

        @classmethod
        def from_connection_string(cls, connection_string):
            calls["connection_string"] = connection_string
            return cls.__new__(cls)

        def create_table_if_not_exists(self, table_name):
            calls["table_name"] = table_name
            return table

    class FakeDefaultAzureCredential:
        pass

    azure = ModuleType("azure")
    azure.__path__ = []
    azure_core = ModuleType("azure.core")
    azure_core.__path__ = []
    azure_core_exceptions = ModuleType("azure.core.exceptions")
    azure_core_exceptions.ResourceNotFoundError = FakeResourceNotFoundError
    azure_data = ModuleType("azure.data")
    azure_data.__path__ = []
    azure_tables = ModuleType("azure.data.tables")
    azure_tables.TableServiceClient = FakeTableServiceClient
    azure_tables.UpdateMode = FakeUpdateMode
    azure_identity = ModuleType("azure.identity")
    azure_identity.DefaultAzureCredential = FakeDefaultAzureCredential

    monkeypatch.setitem(sys.modules, "azure", azure)
    monkeypatch.setitem(sys.modules, "azure.core", azure_core)
    monkeypatch.setitem(sys.modules, "azure.core.exceptions", azure_core_exceptions)
    monkeypatch.setitem(sys.modules, "azure.data", azure_data)
    monkeypatch.setitem(sys.modules, "azure.data.tables", azure_tables)
    monkeypatch.setitem(sys.modules, "azure.identity", azure_identity)
    return table, calls, FakeDefaultAzureCredential


def test_azure_table_store_uses_atomic_replace_upsert(monkeypatch):
    table, calls, _ = install_fake_azure(monkeypatch)
    state = subscription()
    store = AzureTableOneDriveSubscriptionStore(
        "UseDevelopmentStorage=true",
        "Subscriptions",
    )

    store.save(state)

    expected_row_key = hashlib.sha256(state.drive_id.encode("utf-8")).hexdigest()
    assert calls == {
        "connection_string": "UseDevelopmentStorage=true",
        "table_name": "Subscriptions",
    }
    assert table.last_mode == "replace"
    assert ("onedrive-subscriptions", expected_row_key) in table.entities
    assert store.get(state.drive_id) == state
    assert store.get("missing-drive") is None


def test_azure_table_store_supports_managed_identity(monkeypatch):
    _, calls, credential_type = install_fake_azure(monkeypatch)

    AzureTableOneDriveSubscriptionStore(
        None,
        account_url="https://storage.example.com",
    )

    endpoint, credential = calls["identity"]
    assert endpoint == "https://storage.example.com"
    assert isinstance(credential, credential_type)


def test_azure_table_store_requires_storage_configuration(monkeypatch):
    install_fake_azure(monkeypatch)

    with pytest.raises(RuntimeError, match="storage connection string"):
        AzureTableOneDriveSubscriptionStore(None)
