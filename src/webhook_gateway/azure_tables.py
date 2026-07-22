"""Azure Table client helpers shared by durable stores."""

from __future__ import annotations

from typing import Any


def preprovisioned_table_client(service: Any, table_name: str) -> Any:
    """Return a table client without a control-plane create call on hot paths.

    Production tables are provisioned by Bicep. The compatibility fallback keeps
    lightweight test doubles and local emulators working when they only implement
    ``create_table_if_not_exists``.
    """

    getter = getattr(service, "get_table_client", None)
    if callable(getter):
        return getter(table_name)
    return service.create_table_if_not_exists(table_name)
