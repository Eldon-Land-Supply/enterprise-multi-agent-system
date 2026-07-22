from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Lock

import pytest
from azure.core import MatchConditions
from azure.core.exceptions import (
    ResourceExistsError,
    ResourceModifiedError,
    ResourceNotFoundError,
)

from webhook_gateway.quota import AzureTableDailyQuota, MemoryDailyQuota


class FakeEntity(dict):
    def __init__(self, values, etag):
        super().__init__(values)
        self.metadata = {"etag": str(etag)}


class FakeTable:
    def __init__(self):
        self._entities = {}
        self._etag = 0
        self._lock = Lock()
        self.transactions = []
        self.inject_update_conflict = False

    def _next_etag(self):
        self._etag += 1
        return str(self._etag)

    def get_entity(self, partition_key, row_key):
        with self._lock:
            stored = self._entities.get((partition_key, row_key))
            if stored is None:
                raise ResourceNotFoundError("not found")
            values, etag = stored
            return FakeEntity(dict(values), etag)

    def submit_transaction(self, operations):
        with self._lock:
            self.transactions.append(operations)
            if self.inject_update_conflict and operations[0][0] == "update":
                self.inject_update_conflict = False
                partition = operations[0][1]["PartitionKey"]
                counter_values, _ = self._entities[(partition, "quota")]
                counter_values = dict(counter_values)
                counter_values["Count"] += 1
                self._entities[(partition, "quota")] = (
                    counter_values,
                    self._next_etag(),
                )
                self._entities[(partition, "event-external")] = (
                    {
                        "PartitionKey": partition,
                        "RowKey": "event-external",
                    },
                    self._next_etag(),
                )
                raise ResourceModifiedError("etag conflict")

            for operation in operations:
                action, entity = operation[:2]
                key = (entity["PartitionKey"], entity["RowKey"])
                if action == "create" and key in self._entities:
                    raise ResourceExistsError("already exists")
                if action == "update":
                    stored = self._entities.get(key)
                    options = operation[2]
                    if stored is None or options["etag"] != stored[1]:
                        raise ResourceModifiedError("etag conflict")
                    assert options["match_condition"] is MatchConditions.IfNotModified

            for operation in operations:
                _, entity = operation[:2]
                key = (entity["PartitionKey"], entity["RowKey"])
                self._entities[key] = (dict(entity), self._next_etag())
        return []


def fixed_clock(value):
    return lambda: value


def test_memory_quota_is_idempotent_and_repository_scoped():
    quota = MemoryDailyQuota(
        now=fixed_clock(datetime(2026, 7, 22, 12, tzinfo=timezone.utc))
    )

    assert quota.allow("Owner/Repo", "event-1", 2) is True
    assert quota.allow("owner/repo", "event-2", 2) is True
    assert quota.allow("owner/repo", "event-3", 2) is False
    assert quota.allow("OWNER/REPO", "event-1", 2) is True
    assert quota.allow("owner/other", "event-3", 2) is True


def test_memory_quota_resets_on_the_utc_date_boundary():
    current = [datetime(2026, 7, 22, 23, 59, tzinfo=timezone.utc)]
    quota = MemoryDailyQuota(now=lambda: current[0])

    assert quota.allow("owner/repo", "event-1", 1) is True
    assert quota.allow("owner/repo", "event-2", 1) is False
    current[0] += timedelta(minutes=2)
    assert quota.allow("owner/repo", "event-2", 1) is True


def test_memory_quota_never_over_admits_concurrent_events():
    quota = MemoryDailyQuota(
        now=fixed_clock(datetime(2026, 7, 22, tzinfo=timezone.utc))
    )
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(
            pool.map(
                lambda index: quota.allow("owner/repo", f"event-{index}", 7), range(40)
            )
        )

    assert sum(results) == 7


@pytest.mark.parametrize(
    ("repository", "event_id", "limit"),
    [("", "event", 1), ("repo", "", 1), ("repo", "event", 0), ("repo", "event", True)],
)
def test_quota_rejects_invalid_inputs(repository, event_id, limit):
    quota = MemoryDailyQuota()

    with pytest.raises(ValueError):
        quota.allow(repository, event_id, limit)


def test_quota_clock_must_be_timezone_aware():
    quota = MemoryDailyQuota(now=fixed_clock(datetime(2026, 7, 22)))

    with pytest.raises(ValueError, match="timezone-aware"):
        quota.allow("owner/repo", "event", 1)


def test_azure_quota_uses_hashed_safe_keys_and_atomic_idempotent_reservations():
    table = FakeTable()
    quota = AzureTableDailyQuota(
        table_client=table,
        now=fixed_clock(datetime(2026, 7, 22, 12, tzinfo=timezone.utc)),
    )

    assert quota.allow("Owner/Repo#?\\", "delivery/1?#", 1) is True
    assert quota.allow("owner/repo#?\\", "delivery/1?#", 1) is True
    assert quota.allow("owner/repo#?\\", "delivery-2", 1) is False

    first_transaction = table.transactions[0]
    partition = first_transaction[0][1]["PartitionKey"]
    assert len(partition) == 64
    assert all(character in "0123456789abcdef" for character in partition)
    assert "Owner" not in partition
    assert first_transaction[0][0] == "create"
    assert first_transaction[1][0] == "create"
    assert len(table.transactions) == 1


def test_azure_quota_retries_etag_conflict_and_does_not_over_admit():
    table = FakeTable()
    quota = AzureTableDailyQuota(
        table_client=table,
        now=fixed_clock(datetime(2026, 7, 22, 12, tzinfo=timezone.utc)),
    )
    assert quota.allow("owner/repo", "event-1", 2) is True
    table.inject_update_conflict = True

    assert quota.allow("owner/repo", "event-2", 2) is False
    assert table.transactions[1][0][0] == "update"
    assert table.transactions[1][0][2]["etag"]
    assert (
        table.transactions[1][0][2]["match_condition"] is MatchConditions.IfNotModified
    )


def test_azure_quota_allows_an_accepted_event_after_limit_is_reached():
    table = FakeTable()
    quota = AzureTableDailyQuota(
        table_client=table,
        now=fixed_clock(datetime(2026, 7, 22, 12, tzinfo=timezone.utc)),
    )

    assert quota.allow("owner/repo", "event-1", 1) is True
    assert quota.allow("owner/repo", "event-2", 1) is False
    assert quota.allow("owner/repo", "event-1", 1) is True
    assert len(table.transactions) == 1
