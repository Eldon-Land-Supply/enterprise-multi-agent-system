"""Azure Functions v2 webhook ingress and queue workers."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from time import perf_counter
from typing import Any, Mapping
from urllib.parse import urlsplit

import azure.functions as func

from src.webhook_gateway.analysis import OpenAIAnalysisProvider
from src.webhook_gateway.errors import WebhookError
from src.webhook_gateway.events import EventEnvelope
from src.webhook_gateway.intake import WebhookIntake
from src.webhook_gateway.onedrive import (
    DefaultAzureTokenProvider,
    GraphClient,
    GraphRequestError,
)
from src.webhook_gateway.onedrive_state import OneDriveSubscriptionState
from src.webhook_gateway.onedrive_sync import OneDriveDeltaProcessor
from src.webhook_gateway.security import is_configured_secret
from src.webhook_gateway.settings import (
    build_github_policy,
    build_github_quota,
    build_intake_publisher,
    build_onedrive_cursor_store,
    build_onedrive_publisher,
    build_onedrive_subscription_store,
    build_payload_store,
    build_result_publisher,
    build_result_store,
    build_router,
    build_store,
    get_settings,
)
from src.webhook_gateway.worker import EventProcessor, ProcessingResult


app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


@lru_cache(maxsize=1)
def _intake() -> WebhookIntake:
    return WebhookIntake(build_store(), build_intake_publisher())


@lru_cache(maxsize=1)
def _github_intake() -> WebhookIntake:
    settings = get_settings()
    return WebhookIntake(
        build_store(),
        build_intake_publisher(),
        github_policy=build_github_policy(),
        github_quota=build_github_quota(),
        github_daily_limit=settings.github_daily_model_limit,
    )


@lru_cache(maxsize=1)
def _onedrive_intake() -> WebhookIntake:
    return WebhookIntake(
        None,
        build_onedrive_publisher(),
        direct_sources=frozenset({"onedrive"}),
    )


@lru_cache(maxsize=1)
def _graph() -> GraphClient:
    return GraphClient(DefaultAzureTokenProvider())


@lru_cache(maxsize=1)
def _openai_webhook_client() -> Any:
    from openai import OpenAI

    settings = get_settings()
    if not is_configured_secret(settings.openai_webhook_secret):
        raise RuntimeError("OPENAI_WEBHOOK_SECRET is required")
    if not is_configured_secret(settings.openai_api_key):
        raise RuntimeError("OPENAI_API_KEY is required")
    return OpenAI(
        api_key=settings.openai_api_key,
        webhook_secret=settings.openai_webhook_secret,
        timeout=45.0,
        max_retries=0,
    )


@lru_cache(maxsize=1)
def _anthropic_webhook_client() -> Any:
    from anthropic import Anthropic

    api_key = get_settings().anthropic_api_key
    if not is_configured_secret(api_key):
        raise RuntimeError("ANTHROPIC_API_KEY is required")
    return Anthropic(api_key=api_key, timeout=45.0, max_retries=0)


@lru_cache(maxsize=1)
def _processor() -> EventProcessor:
    settings = get_settings()
    openai_provider = None
    if settings.openai_model and is_configured_secret(settings.openai_api_key):
        openai_provider = OpenAIAnalysisProvider(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
        )

    onedrive_processor = None
    if settings.onedrive_drive_id:
        onedrive_processor = OneDriveDeltaProcessor(
            settings.onedrive_drive_id,
            _graph(),
            build_onedrive_cursor_store(),
        )
    return EventProcessor(
        build_router(),
        openai_provider=openai_provider,
        onedrive_processor=onedrive_processor,
        retrieve_openai_callbacks=settings.openai_callback_retrieval,
        processing_store=build_result_store(),
        payloads=build_payload_store(),
    )


def _body(req: func.HttpRequest, *, limit_env: str = "MAX_WEBHOOK_BYTES") -> bytes:
    body = req.get_body()
    default_limit = "26214400" if limit_env == "GITHUB_MAX_WEBHOOK_BYTES" else "1048576"
    max_bytes = int(os.getenv(limit_env, default_limit))
    if len(body) > max_bytes:
        raise WebhookError(413, "payload_too_large", "Webhook payload is too large")
    return body


def _json_response(value: Mapping[str, Any], status: int) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(value, separators=(",", ":")),
        status_code=status,
        mimetype="application/json",
    )


def _error_response(exc: Exception) -> func.HttpResponse:
    if isinstance(exc, WebhookError):
        return _json_response(
            {"error": {"code": exc.code, "message": exc.message}},
            exc.status_code,
        )
    logging.exception("Webhook processing failed")
    return _json_response(
        {"error": {"code": "internal_error", "message": "Request failed"}},
        500,
    )


def _parse_expiration(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "")
        if not text:
            raise RuntimeError("Graph subscription omitted expirationDateTime")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RuntimeError("Graph subscription expiration is invalid") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_subscription_response(state: OneDriveSubscriptionState, reused: bool):
    return {
        "subscription_id": state.subscription_id,
        "drive_id": state.drive_id,
        "resource": state.resource,
        "expiration": state.expiration.isoformat(),
        "reused": reused,
    }


def _normalized_https_url(value: Any) -> str:
    parsed = urlsplit(str(value or "").strip())
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        return ""
    hostname = parsed.hostname.lower()
    netloc = hostname if parsed.port in (None, 443) else f"{hostname}:{parsed.port}"
    path = parsed.path.rstrip("/") or "/"
    query = f"?{parsed.query}" if parsed.query else ""
    return f"https://{netloc}{path}{query}"


def _onedrive_target(settings: Any) -> tuple[str, str, str]:
    if not settings.onedrive_drive_id or not settings.onedrive_tenant_id:
        raise WebhookError(503, "onedrive_not_configured", "OneDrive is not configured")
    if not is_configured_secret(settings.onedrive_client_state):
        raise WebhookError(
            503,
            "onedrive_not_configured",
            "OneDrive client state is not configured",
        )
    base_url = _normalized_https_url(settings.public_base_url)
    if not base_url:
        raise WebhookError(
            503, "invalid_public_url", "PUBLIC_BASE_URL must be an HTTPS origin"
        )
    drive_id = settings.onedrive_drive_id
    return drive_id, f"drives/{drive_id}/root", f"{base_url}/webhooks/onedrive"


def _subscription_matches(
    candidate: Mapping[str, Any], resource: str, notification_url: str
) -> bool:
    if str(candidate.get("resource") or "").lstrip("/") != resource:
        return False
    if str(candidate.get("changeType") or "").lower() != "updated":
        return False
    expected_url = _normalized_https_url(notification_url)
    return bool(
        expected_url
        and _normalized_https_url(candidate.get("notificationUrl")) == expected_url
        and _normalized_https_url(candidate.get("lifecycleNotificationUrl"))
        == expected_url
    )


def _ensure_onedrive_subscription(
    settings: Any, *, seed_delta: bool = True
) -> tuple[OneDriveSubscriptionState, bool]:
    drive_id, resource, notification_url = _onedrive_target(settings)
    graph = _graph()
    cursors = build_onedrive_cursor_store()
    states = build_onedrive_subscription_store()

    if seed_delta and cursors.get(drive_id) is None:
        _, delta_link = graph.list_delta(drive_id, latest_only=True)
        cursors.set(drive_id, delta_link)

    selected: Mapping[str, Any] | None = None
    stale_ids: set[str] = set()
    stored = states.get(drive_id)
    if stored is not None:
        try:
            candidate = graph.get_subscription(stored.subscription_id)
            if _subscription_matches(candidate, resource, notification_url):
                selected = candidate
            elif str(candidate.get("resource") or "").lstrip("/") == resource:
                stale_ids.add(str(candidate.get("id") or stored.subscription_id))
        except GraphRequestError as exc:
            if exc.status not in {404, 410}:
                raise

    if selected is None:
        matches = []
        for item in graph.list_subscriptions():
            if str(item.get("resource") or "").lstrip("/") != resource:
                continue
            if _subscription_matches(item, resource, notification_url):
                matches.append(item)
                continue
            stale_id = str(item.get("id") or "")
            if not stale_id:
                raise RuntimeError("Mismatched Graph subscription omitted id")
            stale_ids.add(stale_id)
        if matches:
            selected = max(
                matches,
                key=lambda item: _parse_expiration(item.get("expirationDateTime")),
            )

    reused = selected is not None
    if selected is not None:
        subscription_id = str(selected.get("id") or "")
        if not subscription_id:
            raise RuntimeError("Graph subscription omitted id")
        expiration = _parse_expiration(selected.get("expirationDateTime"))
        if expiration <= datetime.now(timezone.utc) + timedelta(days=7):
            try:
                selected = graph.renew_subscription(subscription_id, lifetime_days=28)
                expiration = _parse_expiration(selected.get("expirationDateTime"))
            except GraphRequestError as exc:
                if exc.status not in {404, 410}:
                    raise
                selected = None
                reused = False

    if selected is None:
        for stale_id in sorted(stale_ids):
            try:
                graph.delete_subscription(stale_id)
            except GraphRequestError as exc:
                if exc.status not in {404, 410}:
                    raise
        selected = graph.create_drive_subscription(
            drive_id=drive_id,
            notification_url=notification_url,
            lifecycle_notification_url=notification_url,
            client_state=settings.onedrive_client_state,
            lifetime_days=28,
        )
        subscription_id = str(selected.get("id") or "")
        if not subscription_id:
            raise RuntimeError("Graph subscription omitted id")
        expiration = _parse_expiration(selected.get("expirationDateTime"))

    state = OneDriveSubscriptionState(
        subscription_id=subscription_id,
        drive_id=drive_id,
        expiration=expiration,
        resource=resource,
        notification_url=notification_url,
    )
    states.save(state)
    return state, reused


@app.route(route="health", methods=["GET"])
def health(_: func.HttpRequest) -> func.HttpResponse:
    settings = get_settings()
    subscription_ready = False
    if settings.onedrive_drive_id:
        try:
            drive_id, resource, notification_url = _onedrive_target(settings)
            state = build_onedrive_subscription_store().get(drive_id)
            subscription_ready = bool(
                state
                and state.expiration > datetime.now(timezone.utc)
                and state.resource == resource
                and _normalized_https_url(state.notification_url)
                == _normalized_https_url(notification_url)
            )
        except Exception:
            logging.exception("OneDrive state health check failed")
    configuration = {
        "github_webhook": is_configured_secret(settings.github_webhook_secret),
        "github_admission": bool(
            settings.github_allowed_repository_ids and settings.github_allowed_events
        ),
        "openai_webhook": is_configured_secret(settings.openai_webhook_secret),
        "anthropic_webhook": is_configured_secret(
            settings.anthropic_webhook_signing_key
        ),
        "onedrive_webhook": bool(
            is_configured_secret(settings.onedrive_client_state)
            and settings.onedrive_drive_id
            and settings.onedrive_tenant_id
            and settings.public_base_url
            and subscription_ready
        ),
        "queue": bool(
            settings.service_bus_connection or settings.service_bus_namespace
        ),
        "ai_provider": settings.ai_provider,
    }
    provider_ready = {
        "openai": is_configured_secret(settings.openai_api_key)
        and bool(settings.openai_model),
        "claude": is_configured_secret(settings.anthropic_api_key)
        and bool(settings.anthropic_model),
    }
    if settings.ai_provider == "both":
        configuration["ai_runtime"] = all(provider_ready.values())
    else:
        configuration["ai_runtime"] = provider_ready.get(settings.ai_provider, False)
    ready = all(
        configuration[name]
        for name in (
            "github_webhook",
            "github_admission",
            "openai_webhook",
            "anthropic_webhook",
            "onedrive_webhook",
            "queue",
            "ai_runtime",
        )
    )
    return _json_response(
        {"status": "ready" if ready else "configuration_required", **configuration},
        200 if ready else 503,
    )


@app.route(route="webhooks/github", methods=["POST"])
def github_webhook(req: func.HttpRequest) -> func.HttpResponse:
    try:
        settings = get_settings()
        result = _github_intake().github(
            _body(req, limit_env="GITHUB_MAX_WEBHOOK_BYTES"),
            dict(req.headers),
            settings.github_webhook_secret,
            previous_secret=settings.github_webhook_previous_secret,
        )
        return _json_response(result.to_dict(), 200)
    except Exception as exc:
        return _error_response(exc)


@app.route(route="webhooks/events", methods=["POST"])
def generic_webhook(req: func.HttpRequest) -> func.HttpResponse:
    try:
        result = _intake().generic(
            _body(req), dict(req.headers), get_settings().inbound_webhook_secret
        )
        return _json_response(result.to_dict(), 202)
    except Exception as exc:
        return _error_response(exc)


@app.route(route="webhooks/delivery", methods=["POST"])
def delivery_webhook(req: func.HttpRequest) -> func.HttpResponse:
    try:
        result = _intake().generic(
            _body(req),
            dict(req.headers),
            get_settings().inbound_webhook_secret,
            source="delivery",
        )
        return _json_response(result.to_dict(), 202)
    except Exception as exc:
        return _error_response(exc)


@app.route(route="webhooks/openai", methods=["POST"])
def openai_webhook(req: func.HttpRequest) -> func.HttpResponse:
    try:
        raw_body = _body(req).decode("utf-8")
        event = _openai_webhook_client().webhooks.unwrap(raw_body, dict(req.headers))
        payload = event.model_dump() if hasattr(event, "model_dump") else dict(event)
        result = _intake().verified_openai(
            payload, delivery_id=req.headers.get("webhook-id")
        )
        return _json_response(result.to_dict(), 200)
    except Exception as exc:
        try:
            from openai import InvalidWebhookSignatureError

            if isinstance(exc, InvalidWebhookSignatureError):
                return _json_response(
                    {
                        "error": {
                            "code": "invalid_signature",
                            "message": "Invalid signature",
                        }
                    },
                    400,
                )
        except ImportError:
            pass
        # The SDK uses ValueError for missing/malformed webhook headers. Intake
        # and infrastructure failures use WebhookError/RuntimeError instead.
        if isinstance(exc, ValueError):
            return _json_response(
                {
                    "error": {
                        "code": "invalid_signature",
                        "message": "Invalid signature",
                    }
                },
                400,
            )
        return _error_response(exc)


@app.route(route="webhooks/anthropic", methods=["POST"])
def anthropic_webhook(req: func.HttpRequest) -> func.HttpResponse:
    try:
        raw_body = _body(req).decode("utf-8")
        settings = get_settings()
        if not is_configured_secret(settings.anthropic_webhook_signing_key):
            raise RuntimeError("ANTHROPIC_WEBHOOK_SIGNING_KEY is required")
        event = _anthropic_webhook_client().beta.webhooks.unwrap(
            raw_body,
            headers=dict(req.headers),
            key=settings.anthropic_webhook_signing_key,
        )
        payload = event.model_dump() if hasattr(event, "model_dump") else dict(event)
        result = _intake().verified_anthropic(payload)
        return _json_response(result.to_dict(), 200)
    except Exception as exc:
        try:
            from anthropic import APIWebhookValidationError

            if isinstance(exc, APIWebhookValidationError):
                return _json_response(
                    {
                        "error": {
                            "code": "invalid_signature",
                            "message": "Invalid signature",
                        }
                    },
                    400,
                )
        except ImportError:
            pass
        return _error_response(exc)


@app.route(route="webhooks/onedrive", methods=["POST"])
def onedrive_webhook(req: func.HttpRequest) -> func.HttpResponse:
    validation_token = req.params.get("validationToken")
    if validation_token is not None:
        return func.HttpResponse(
            validation_token,
            status_code=200,
            mimetype="text/plain",
        )
    started = perf_counter()
    try:
        settings = get_settings()
        if not settings.onedrive_drive_id:
            raise WebhookError(
                503, "onedrive_not_configured", "OneDrive is not configured"
            )
        state = build_onedrive_subscription_store().get(settings.onedrive_drive_id)
        if state is None:
            raise WebhookError(
                503, "onedrive_not_bootstrapped", "OneDrive is not bootstrapped"
            )
        result = _onedrive_intake().onedrive(
            _body(req),
            settings.onedrive_client_state,
            expected_subscription_id=state.subscription_id,
            expected_resource=state.resource,
            expected_tenant_id=settings.onedrive_tenant_id,
        )
        elapsed_ms = round((perf_counter() - started) * 1000)
        logging.info(
            "OneDrive webhook accepted enqueued=%s elapsed_ms=%s",
            result.accepted,
            elapsed_ms,
        )
        if elapsed_ms > 2_000:
            logging.warning("OneDrive webhook acknowledgement exceeded 2 seconds")
        return _json_response(result.to_dict(), 202)
    except Exception as exc:
        return _error_response(exc)


@app.route(
    route="admin/onedrive/bootstrap",
    methods=["POST"],
    auth_level=func.AuthLevel.FUNCTION,
)
def bootstrap_onedrive(_: func.HttpRequest) -> func.HttpResponse:
    """Seed delta and create/reuse a Graph subscription as the Function identity."""

    try:
        state, reused = _ensure_onedrive_subscription(get_settings())
        return _json_response(_safe_subscription_response(state, reused), 200)
    except Exception as exc:
        return _error_response(exc)


@app.timer_trigger(
    schedule="0 */1 * * * *",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def dispatch_pending_webhooks(timer: func.TimerRequest) -> None:
    """Recover intake records left pending by a crash or ambiguous queue send."""

    store = build_store()
    publisher = build_intake_publisher()
    for event in store.pending(limit=100):
        try:
            publisher.publish(event)
            store.mark_sent(event)
        except Exception:
            logging.exception(
                "Pending intake dispatch failed source=%s event_id=%s",
                event.source,
                event.id,
            )


def _dispatch_result_record(private_event: EventEnvelope) -> None:
    payloads = build_payload_store()
    resolved = payloads.resolve(private_event) if payloads else private_event
    result = ProcessingResult.from_private_record(resolved.data)
    public_event = EventEnvelope(
        id=f"{private_event.id}:result",
        source="webhook-gateway",
        type="event.processed",
        data=result.to_dict(),
        correlation_id=result.correlation_id,
        received_at=private_event.received_at,
        subject=private_event.subject,
    )
    build_result_publisher().publish(public_event)
    try:
        _processor().acknowledge(result)
    except RuntimeError:
        # The public result is already durable. Leaving the cursor unchanged makes
        # the delta range replayable; do not strand the completed result forever.
        logging.exception(
            "OneDrive cursor acknowledgement failed after result publication; "
            "delta changes will be replayed"
        )
    build_result_store().mark_sent(private_event)


@app.timer_trigger(
    schedule="30 */1 * * * *",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def dispatch_pending_results(timer: func.TimerRequest) -> None:
    """Publish exact stored result winners without rerunning providers or Graph."""

    for event in build_result_store().pending(limit=100):
        try:
            _dispatch_result_record(event)
        except Exception:
            logging.exception("Pending result dispatch failed event_id=%s", event.id)


def _enqueue_onedrive_reconciliation(
    settings: Any, state: OneDriveSubscriptionState | None
) -> None:
    drive_id = settings.onedrive_drive_id
    if not drive_id:
        return
    resource = state.resource if state else f"drives/{drive_id}/root"
    subscription_id = state.subscription_id if state else f"drive:{drive_id}"
    now = datetime.now(timezone.utc)
    event = EventEnvelope(
        id=f"reconcile:{drive_id}:{now.strftime('%Y%m%d%H')}",
        source="onedrive",
        type="drive.updated",
        data={
            "subscriptionId": subscription_id,
            "resource": resource,
            "tenantId": settings.onedrive_tenant_id,
            "reason": "scheduled_reconciliation",
        },
        correlation_id=subscription_id,
        subject=resource,
    )
    store = build_store()
    staged = store.stage(event)
    if staged.state != "sent":
        build_intake_publisher().publish(staged.event)
        store.mark_sent(staged.event)


@app.timer_trigger(
    schedule="0 17 */12 * * *",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def maintain_onedrive_subscription(timer: func.TimerRequest) -> None:
    """Repair/renew subscriptions and independently enqueue delta reconciliation."""

    settings = get_settings()
    if not settings.onedrive_drive_id:
        logging.warning("OneDrive maintenance skipped: drive is not configured")
        return
    states = build_onedrive_subscription_store()
    state = states.get(settings.onedrive_drive_id)
    try:
        state, _ = _ensure_onedrive_subscription(settings)
    except Exception:
        logging.exception(
            "OneDrive subscription repair failed; continuing with delta reconciliation"
        )
    _enqueue_onedrive_reconciliation(settings, state)


@app.service_bus_queue_trigger(
    arg_name="message",
    queue_name="%SERVICE_BUS_QUEUE_NAME%",
    connection="SERVICE_BUS_CONNECTION",
)
def process_event(message: func.ServiceBusMessage) -> None:
    queued_event = EventEnvelope.from_json(message.get_body())
    payloads = build_payload_store()
    event = payloads.resolve(queued_event) if payloads else queued_event
    store = build_result_store()
    private_id = f"{event.source}:{event.id}"
    staged = store.get("result-outbox", private_id)
    if staged is None:
        processor = _processor()
        result = processor.process(event)
        private_event = EventEnvelope(
            id=private_id,
            source="result-outbox",
            type="processing.completed",
            data=result.to_private_record(),
            correlation_id=event.correlation_id,
            received_at=event.received_at,
            subject=event.subject,
        )
        staged = store.stage(private_event)
        if not staged.created:
            processor.abandon(result)
    if staged.state == "sent":
        return
    try:
        _dispatch_result_record(staged.event)
    except Exception:
        # The private result and OneDrive checkpoint are durable. The result
        # dispatcher retries the exact winner without rerunning paid model calls.
        logging.exception("Result dispatch deferred event_id=%s", staged.event.id)
