import pytest

from webhook_gateway.analysis import ProviderRouter, validate_analysis
from webhook_gateway.events import EventEnvelope
from webhook_gateway.idempotency import MemoryIdempotencyStore
from webhook_gateway.worker import EventProcessor


class CountingProvider:
    def __init__(self, name, *, fail_once=False):
        self.name = name
        self.calls = 0
        self.fail_once = fail_once

    def analyze(self, event):
        self.calls += 1
        if self.fail_once and self.calls == 1:
            raise RuntimeError(f"{self.name} unavailable")
        return validate_analysis(
            {
                "summary": f"{self.name} summary",
                "category": "operations",
                "urgency": "low",
                "recommended_action": "Review",
                "requires_human_approval": True,
            },
            self.name,
            f"{self.name}-model",
        )


def test_both_mode_reuses_first_provider_checkpoint_after_second_fails():
    openai = CountingProvider("openai")
    claude = CountingProvider("claude", fail_once=True)
    processor = EventProcessor(
        ProviderRouter({"openai": openai, "claude": claude}, default_provider="both"),
        processing_store=MemoryIdempotencyStore(existing_payload_policy="first_wins"),
    )
    event = EventEnvelope("evt-1", "github", "push", {}, "evt-1")

    with pytest.raises(RuntimeError, match="claude unavailable"):
        processor.process(event)

    result = processor.process(event)

    assert openai.calls == 1
    assert claude.calls == 2
    assert [item["provider"] for item in result.output["analyses"]] == [
        "openai",
        "claude",
    ]
