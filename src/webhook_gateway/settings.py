"""Environment configuration and runtime dependency factories."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from .analysis import ClaudeAnalysisProvider, OpenAIAnalysisProvider, ProviderRouter
from .github_admission import GitHubAdmissionPolicy
from .idempotency import (
    AzureTableIdempotencyStore,
    IdempotencyStore,
    MemoryIdempotencyStore,
    SqliteIdempotencyStore,
)
from .onedrive_state import (
    AzureTableOneDriveSubscriptionStore,
    MemoryOneDriveSubscriptionStore,
    OneDriveSubscriptionStore,
)
from .onedrive_sync import AzureTableDeltaCursorStore, MemoryDeltaCursorStore
from .payloads import AzureBlobEventPayloadStore, EventPayloadStore
from .queueing import AzureServiceBusPublisher, EventPublisher
from .quota import AzureTableDailyQuota, DailyQuota, MemoryDailyQuota
from .security import is_configured_secret


def _csv(name: str, default: str = "") -> frozenset[str]:
    return frozenset(
        item.strip() for item in os.getenv(name, default).split(",") if item.strip()
    )


def _positive_int(name: str, default: int, *, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not 1 <= value <= maximum:
        raise RuntimeError(f"{name} must be between 1 and {maximum}")
    return value


@dataclass(frozen=True)
class Settings:
    app_env: str
    public_base_url: str
    github_webhook_secret: str
    github_webhook_previous_secret: str
    github_allowed_repository_ids: frozenset[str]
    github_allowed_events: frozenset[str]
    github_trusted_associations: frozenset[str]
    github_allowed_app_ids: frozenset[str]
    github_daily_model_limit: int
    github_quota_table_name: str
    inbound_webhook_secret: str
    openai_webhook_secret: str
    anthropic_webhook_signing_key: str
    onedrive_client_state: str
    onedrive_drive_id: str | None
    onedrive_tenant_id: str | None
    onedrive_subscription_table_name: str
    service_bus_queue_name: str
    service_bus_result_queue_name: str
    service_bus_connection: str | None
    service_bus_namespace: str | None
    idempotency_backend: str
    idempotency_connection: str | None
    idempotency_account_url: str | None
    idempotency_table_name: str
    result_outbox_table_name: str
    sqlite_path: str
    result_sqlite_path: str
    payload_storage_connection: str | None
    payload_storage_account_url: str | None
    payload_blob_container: str
    ai_provider: str
    openai_api_key: str | None
    openai_model: str | None
    openai_callback_retrieval: bool
    anthropic_api_key: str | None
    anthropic_model: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            app_env=os.getenv("APP_ENV", "development"),
            public_base_url=os.getenv("PUBLIC_BASE_URL", "").rstrip("/"),
            github_webhook_secret=os.getenv("GITHUB_WEBHOOK_SECRET", ""),
            github_webhook_previous_secret=os.getenv(
                "GITHUB_WEBHOOK_PREVIOUS_SECRET", ""
            ),
            github_allowed_repository_ids=_csv("GITHUB_ALLOWED_REPOSITORY_IDS"),
            github_allowed_events=_csv("GITHUB_ALLOWED_EVENTS", "push"),
            github_trusted_associations=_csv(
                "GITHUB_TRUSTED_AUTHOR_ASSOCIATIONS", "OWNER,MEMBER,COLLABORATOR"
            ),
            github_allowed_app_ids=_csv("GITHUB_ALLOWED_APP_IDS"),
            github_daily_model_limit=_positive_int(
                "GITHUB_DAILY_MODEL_LIMIT", 100, maximum=10_000
            ),
            github_quota_table_name=os.getenv(
                "GITHUB_QUOTA_TABLE_NAME", "GitHubDailyModelQuota"
            ),
            inbound_webhook_secret=os.getenv("INBOUND_WEBHOOK_SECRET", ""),
            openai_webhook_secret=os.getenv("OPENAI_WEBHOOK_SECRET", ""),
            anthropic_webhook_signing_key=os.getenv(
                "ANTHROPIC_WEBHOOK_SIGNING_KEY", ""
            ),
            onedrive_client_state=os.getenv("ONEDRIVE_CLIENT_STATE", ""),
            onedrive_drive_id=os.getenv("ONEDRIVE_DRIVE_ID"),
            onedrive_tenant_id=os.getenv("ONEDRIVE_TENANT_ID"),
            onedrive_subscription_table_name=os.getenv(
                "ONEDRIVE_SUBSCRIPTION_TABLE_NAME", "OneDriveSubscriptions"
            ),
            service_bus_queue_name=os.getenv("SERVICE_BUS_QUEUE_NAME", "ai-events"),
            service_bus_result_queue_name=os.getenv(
                "SERVICE_BUS_RESULT_QUEUE_NAME", "ai-results"
            ),
            service_bus_connection=os.getenv("SERVICE_BUS_CONNECTION"),
            service_bus_namespace=os.getenv("SERVICE_BUS_FULLY_QUALIFIED_NAMESPACE"),
            idempotency_backend=os.getenv("IDEMPOTENCY_BACKEND", "sqlite"),
            idempotency_connection=os.getenv("IDEMPOTENCY_STORAGE_CONNECTION")
            or os.getenv("AzureWebJobsStorage"),
            idempotency_account_url=os.getenv("IDEMPOTENCY_STORAGE_ACCOUNT_URL"),
            idempotency_table_name=os.getenv(
                "IDEMPOTENCY_TABLE_NAME", "WebhookDeliveries"
            ),
            result_outbox_table_name=os.getenv(
                "RESULT_OUTBOX_TABLE_NAME", "WebhookResults"
            ),
            sqlite_path=os.getenv("IDEMPOTENCY_SQLITE_PATH", ".webhook-deliveries.db"),
            result_sqlite_path=os.getenv(
                "RESULT_OUTBOX_SQLITE_PATH", ".webhook-results.db"
            ),
            payload_storage_connection=os.getenv("PAYLOAD_STORAGE_CONNECTION")
            or os.getenv("IDEMPOTENCY_STORAGE_CONNECTION")
            or os.getenv("AzureWebJobsStorage"),
            payload_storage_account_url=os.getenv("PAYLOAD_STORAGE_ACCOUNT_URL")
            or os.getenv("IDEMPOTENCY_STORAGE_ACCOUNT_URL"),
            payload_blob_container=os.getenv(
                "PAYLOAD_BLOB_CONTAINER", "webhook-payloads"
            ),
            ai_provider=os.getenv("AI_PROVIDER", "openai"),
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            openai_model=os.getenv("OPENAI_MODEL"),
            openai_callback_retrieval=os.getenv(
                "OPENAI_CALLBACK_RETRIEVAL", "false"
            ).lower()
            in {"1", "true", "yes"},
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
            anthropic_model=os.getenv("ANTHROPIC_MODEL"),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


@lru_cache(maxsize=1)
def build_payload_store() -> EventPayloadStore | None:
    settings = get_settings()
    if (
        not settings.payload_storage_connection
        and not settings.payload_storage_account_url
    ):
        if settings.app_env.lower() == "production":
            raise RuntimeError("Payload storage is required in production")
        return None
    return AzureBlobEventPayloadStore(
        settings.payload_storage_connection,
        settings.payload_blob_container,
        account_url=settings.payload_storage_account_url,
    )


def _table_store(table_name: str, *, first_wins: bool) -> IdempotencyStore:
    settings = get_settings()
    payloads = build_payload_store()
    if payloads is None:
        raise RuntimeError("Azure Table outbox requires payload storage")
    return AzureTableIdempotencyStore(
        settings.idempotency_connection,
        table_name,
        payloads,
        account_url=settings.idempotency_account_url,
        existing_payload_policy="first_wins" if first_wins else "reject",
    )


@lru_cache(maxsize=1)
def build_store() -> IdempotencyStore:
    settings = get_settings()
    backend = settings.idempotency_backend.lower()
    if backend == "memory":
        if settings.app_env.lower() == "production":
            raise RuntimeError("Memory idempotency is not allowed in production")
        return MemoryIdempotencyStore()
    if backend == "sqlite":
        if settings.app_env.lower() == "production":
            raise RuntimeError("SQLite idempotency is not allowed in production")
        return SqliteIdempotencyStore(settings.sqlite_path)
    if backend == "azure_table":
        return _table_store(settings.idempotency_table_name, first_wins=False)
    raise RuntimeError(f"Unsupported IDEMPOTENCY_BACKEND: {backend}")


@lru_cache(maxsize=1)
def build_result_store() -> IdempotencyStore:
    settings = get_settings()
    backend = settings.idempotency_backend.lower()
    if backend == "memory":
        if settings.app_env.lower() == "production":
            raise RuntimeError("Memory result outbox is not allowed in production")
        return MemoryIdempotencyStore(existing_payload_policy="first_wins")
    if backend == "sqlite":
        if settings.app_env.lower() == "production":
            raise RuntimeError("SQLite result outbox is not allowed in production")
        return SqliteIdempotencyStore(
            settings.result_sqlite_path, existing_payload_policy="first_wins"
        )
    if backend == "azure_table":
        return _table_store(settings.result_outbox_table_name, first_wins=True)
    raise RuntimeError(f"Unsupported IDEMPOTENCY_BACKEND: {backend}")


@lru_cache(maxsize=1)
def build_github_quota() -> DailyQuota:
    settings = get_settings()
    if settings.idempotency_backend.lower() == "azure_table":
        return AzureTableDailyQuota(
            settings.idempotency_connection,
            settings.github_quota_table_name,
            account_url=settings.idempotency_account_url,
        )
    if settings.app_env.lower() == "production":
        raise RuntimeError("Production GitHub quota requires Azure Table storage")
    return MemoryDailyQuota()


@lru_cache(maxsize=1)
def build_github_policy() -> GitHubAdmissionPolicy:
    settings = get_settings()
    return GitHubAdmissionPolicy(
        set(settings.github_allowed_repository_ids),
        set(settings.github_allowed_events),
        trusted_associations=set(settings.github_trusted_associations),
        allowed_app_ids=set(settings.github_allowed_app_ids),
    )


@lru_cache(maxsize=1)
def build_onedrive_subscription_store() -> OneDriveSubscriptionStore:
    settings = get_settings()
    if settings.idempotency_backend.lower() == "azure_table":
        return AzureTableOneDriveSubscriptionStore(
            settings.idempotency_connection,
            settings.onedrive_subscription_table_name,
            account_url=settings.idempotency_account_url,
        )
    if settings.app_env.lower() == "production":
        raise RuntimeError("Production OneDrive state requires Azure Table storage")
    return MemoryOneDriveSubscriptionStore()


@lru_cache(maxsize=1)
def build_onedrive_cursor_store():
    settings = get_settings()
    if settings.idempotency_backend.lower() == "azure_table":
        return AzureTableDeltaCursorStore(
            settings.idempotency_connection,
            account_url=settings.idempotency_account_url,
        )
    if settings.app_env.lower() == "production":
        raise RuntimeError("Production OneDrive sync requires Azure Table cursors")
    return MemoryDeltaCursorStore()


def _service_bus_publisher(
    queue_name: str, *, offload_payloads: bool = True
) -> EventPublisher:
    settings = get_settings()
    return AzureServiceBusPublisher(
        queue_name,
        connection_string=settings.service_bus_connection,
        fully_qualified_namespace=settings.service_bus_namespace,
        payloads=build_payload_store() if offload_payloads else None,
    )


@lru_cache(maxsize=1)
def build_intake_publisher() -> EventPublisher:
    return _service_bus_publisher(get_settings().service_bus_queue_name)


@lru_cache(maxsize=1)
def build_result_publisher() -> EventPublisher:
    return _service_bus_publisher(get_settings().service_bus_result_queue_name)


@lru_cache(maxsize=1)
def build_onedrive_publisher() -> EventPublisher:
    return _service_bus_publisher(
        get_settings().service_bus_queue_name, offload_payloads=False
    )


@lru_cache(maxsize=1)
def build_router() -> ProviderRouter:
    settings = get_settings()
    providers = {}
    if settings.ai_provider in {"openai", "both"}:
        if not is_configured_secret(settings.openai_api_key):
            raise RuntimeError("OPENAI_API_KEY is required")
        providers["openai"] = OpenAIAnalysisProvider(
            model=settings.openai_model or "",
            api_key=settings.openai_api_key,
        )
    if settings.ai_provider in {"claude", "both"}:
        if not is_configured_secret(settings.anthropic_api_key):
            raise RuntimeError("ANTHROPIC_API_KEY is required")
        providers["claude"] = ClaudeAnalysisProvider(
            model=settings.anthropic_model or "",
            api_key=settings.anthropic_api_key,
        )
    return ProviderRouter(providers, default_provider=settings.ai_provider)
