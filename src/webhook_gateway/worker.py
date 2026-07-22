"""Queue worker that routes canonical events to AI and OneDrive processors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from .analysis import OpenAIAnalysisProvider, ProviderRouter, validate_analysis
from .events import EventEnvelope
from .idempotency import IdempotencyStore
from .payloads import EventPayloadStore


OPENAI_TERMINAL_EVENTS = {
    "response.completed",
    "response.failed",
    "response.cancelled",
    "response.incomplete",
}


class OneDriveProcessor(Protocol):
    def process(self, event: EventEnvelope) -> Mapping[str, Any]: ...

    def acknowledge(self, checkpoint: Mapping[str, Any]) -> None: ...

    def abandon(self, checkpoint: Mapping[str, Any]) -> None: ...


@dataclass(frozen=True)
class ProcessingResult:
    event_id: str
    correlation_id: str
    status: str
    output: Mapping[str, Any]
    checkpoint: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "correlation_id": self.correlation_id,
            "status": self.status,
            "output": dict(self.output),
        }

    def to_private_record(self) -> dict[str, Any]:
        """Serialize durable internal state; checkpoints never enter public results."""

        return {**self.to_dict(), "checkpoint": dict(self.checkpoint)}

    @classmethod
    def from_private_record(cls, value: Mapping[str, Any]) -> "ProcessingResult":
        output = value.get("output")
        checkpoint = value.get("checkpoint", {})
        if not isinstance(output, Mapping) or not isinstance(checkpoint, Mapping):
            raise ValueError("Invalid stored processing result")
        event_id = str(value.get("event_id") or "")
        correlation_id = str(value.get("correlation_id") or "")
        status = str(value.get("status") or "")
        if not event_id or not correlation_id or not status:
            raise ValueError("Stored processing result is missing required fields")
        return cls(
            event_id=event_id,
            correlation_id=correlation_id,
            status=status,
            output=dict(output),
            checkpoint=dict(checkpoint),
        )


class EventProcessor:
    def __init__(
        self,
        router: ProviderRouter,
        openai_provider: OpenAIAnalysisProvider | None = None,
        onedrive_processor: OneDriveProcessor | None = None,
        retrieve_openai_callbacks: bool = False,
        processing_store: IdempotencyStore | None = None,
        payloads: EventPayloadStore | None = None,
    ) -> None:
        self._router = router
        self._openai = openai_provider
        self._onedrive = onedrive_processor
        self._retrieve_openai_callbacks = retrieve_openai_callbacks
        self._processing_store = processing_store
        self._payloads = payloads

    def process(self, event: EventEnvelope) -> ProcessingResult:
        if event.source == "openai" and event.type in OPENAI_TERMINAL_EVENTS:
            return self._process_openai_callback(event)
        if event.source == "onedrive":
            if self._onedrive is None:
                raise RuntimeError("OneDrive delta processor is not configured")
            output = dict(self._onedrive.process(event))
            checkpoint = output.pop("_checkpoint", {})
            return ProcessingResult(
                event_id=event.id,
                correlation_id=event.correlation_id,
                status="completed",
                output=output,
                checkpoint=checkpoint,
            )

        analyses = [item.to_dict() for item in self._analyze(event)]
        return ProcessingResult(
            event_id=event.id,
            correlation_id=event.correlation_id,
            status="completed",
            output={"analyses": analyses},
        )

    def _analyze(self, event: EventEnvelope):
        if self._processing_store is None:
            return self._router.analyze(event)

        results = []
        for provider_name in self._router.provider_names(event):
            checkpoint_id = f"{event.source}:{event.id}:{provider_name}"
            staged = self._processing_store.get("analysis-checkpoint", checkpoint_id)
            if staged is None:
                result = self._router.analyze_provider(event, provider_name)
                checkpoint_event = EventEnvelope(
                    id=checkpoint_id,
                    source="analysis-checkpoint",
                    type="analysis.completed",
                    data=result.to_dict(),
                    correlation_id=event.correlation_id,
                    received_at=event.received_at,
                    subject=event.subject,
                )
                staged = self._processing_store.stage(checkpoint_event)
            if staged.state != "sent":
                self._processing_store.mark_sent(staged.event)
            stored = (
                self._payloads.resolve(staged.event) if self._payloads else staged.event
            )
            provider = str(stored.data.get("provider") or "")
            model = str(stored.data.get("model") or "")
            if provider != provider_name or not model:
                raise RuntimeError("Stored analysis checkpoint has invalid identity")
            results.append(validate_analysis(stored.data, provider, model))
        return results

    def acknowledge(self, result: ProcessingResult) -> None:
        if result.checkpoint and self._onedrive is not None:
            self._onedrive.acknowledge(result.checkpoint)

    def abandon(self, result: ProcessingResult) -> None:
        if result.checkpoint and self._onedrive is not None:
            self._onedrive.abandon(result.checkpoint)

    def _process_openai_callback(self, event: EventEnvelope) -> ProcessingResult:
        response_id = str(event.data.get("id") or "")
        if not response_id:
            raise ValueError("OpenAI callback is missing response id")
        if event.type == "response.completed":
            if not self._retrieve_openai_callbacks:
                return ProcessingResult(
                    event_id=event.id,
                    correlation_id=event.correlation_id,
                    status="completed",
                    output={
                        "response_id": response_id,
                        "retrieval": "disabled_by_default",
                    },
                )
            if self._openai is None:
                raise RuntimeError("OpenAI provider is required to retrieve a response")
            response = self._openai.retrieve_background(response_id)
            approved = {
                key: response.get(key)
                for key in ("id", "status", "model", "output_text")
                if key in response
            }
            return ProcessingResult(
                event_id=event.id,
                correlation_id=event.correlation_id,
                status="completed",
                output={"response": approved},
            )
        return ProcessingResult(
            event_id=event.id,
            correlation_id=event.correlation_id,
            status=event.type.removeprefix("response."),
            output={"response_id": response_id, "event": dict(event.data)},
        )
