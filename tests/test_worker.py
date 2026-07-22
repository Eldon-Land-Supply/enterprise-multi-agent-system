from types import SimpleNamespace

from webhook_gateway.analysis import ProviderRouter, validate_analysis
from webhook_gateway.events import EventEnvelope
from webhook_gateway.worker import EventProcessor


class Provider:
    name = "openai"

    def analyze(self, event):
        return validate_analysis(
            {
                "summary": "Summary",
                "category": "operations",
                "urgency": "low",
                "recommended_action": "Review",
                "requires_human_approval": False,
            },
            self.name,
            "model",
        )


def test_worker_routes_business_event_to_configured_provider():
    processor = EventProcessor(ProviderRouter({"openai": Provider()}))
    event = EventEnvelope(
        id="evt-1",
        source="github",
        type="push",
        data={},
        correlation_id="evt-1",
    )

    result = processor.process(event)

    assert result.status == "completed"
    assert result.output["analyses"][0]["provider"] == "openai"


def test_worker_retrieves_completed_background_response():
    openai_provider = SimpleNamespace(
        retrieve_background=lambda response_id: {
            "id": response_id,
            "status": "completed",
            "output_text": "done",
        }
    )
    processor = EventProcessor(
        ProviderRouter({"openai": Provider()}),
        openai_provider=openai_provider,
        retrieve_openai_callbacks=True,
    )
    event = EventEnvelope(
        id="wh_1",
        source="openai",
        type="response.completed",
        data={"id": "resp_1"},
        correlation_id="resp_1",
    )

    result = processor.process(event)

    assert result.output["response"]["id"] == "resp_1"


def test_worker_records_non_success_background_terminal_state():
    processor = EventProcessor(ProviderRouter({"openai": Provider()}))
    event = EventEnvelope(
        id="wh_2",
        source="openai",
        type="response.incomplete",
        data={"id": "resp_2"},
        correlation_id="resp_2",
    )

    result = processor.process(event)

    assert result.status == "incomplete"
