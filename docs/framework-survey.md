# Framework Survey: Provider Abstraction & Queue-Aligned Orchestration

This survey reviews candidate frameworks for orchestrating and coordinating agents in the enterprise system. The focus is on (1) compatibility with a provider abstraction that can swap LLMs or tool backends without touching business flows and (2) fit with a queueing model (e.g., Azure Service Bus/Storage queues) that enables durable, decoupled execution.

## Evaluation Criteria
- **Provider abstraction** – native support for pluggable LLM/tool backends, deterministic configuration of models, and the ability to externalize credentials/config.
- **Queue alignment** – ability to send/receive messages via queues or event buses, including idempotency hooks and correlation IDs.
- **Operational readiness** – monitoring, deployment model (container, Azure Functions), governance fit (network isolation, private endpoints), and SDK maturity.
- **Extensibility** – ease of writing adapters for custom providers or queue transports.

## Semantic Kernel (Microsoft)
- **Fit with provider abstraction**: Offers `IAIService`/`ChatCompletionService` abstractions for Azure OpenAI/OpenAI and community providers. Configuration objects allow swapping deployments without changing skills. Good match for a repository-level provider registry.
- **Queueing model**: No built-in queue router, but kernels can be hosted in Azure Functions/Containers and invoked via Service Bus-triggered Functions. Planner steps can be persisted to tables/queues for resumable flows.
- **Prototype/steps**:
  1. Define a provider registry implementing `IAIService` that reads deployment names from configuration (Key Vault/App Config).
  2. Wrap kernel execution inside an Azure Function triggered by Service Bus; deserialize messages into kernel context variables.
  3. Emit downstream tasks by enqueueing messages with correlation IDs and SK context state serialized to storage.
- **Pros**: First-party Microsoft support; rich skills/tooling model; aligns with C#/Python stacks; good for enterprise security (managed identity, private networking).
- **Cons/Gaps**: Multi-agent patterns are manual; planner components can be heavy for simple flows; no native queue primitives (requires Function glue).

## AutoGen (Microsoft Research)
- **Fit with provider abstraction**: Supports multiple model clients (Azure OpenAI/OpenAI/others) via `llm_config` dictionaries. Tool calling uses Python callables, so a provider factory can inject clients per agent.
- **Queueing model**: Conversation loop is synchronous in-process. To align with queued execution, agents must persist dialogue state and resume from queue-triggered workers; no out-of-the-box queue connector.
- **Prototype/steps**:
  1. Create an `LLMProviderFactory` that returns configured Azure OpenAI clients per agent role.
  2. Persist `Conversation` state (messages, sender) to Blob/Table storage after each turn; enqueue next-turn work item onto Service Bus with a correlation ID.
  3. Service Bus-triggered worker restores the conversation and calls `agent.step()` to continue.
- **Pros**: Strong multi-agent primitives; easy tool registration; active examples for agent collaboration.
- **Cons/Gaps**: Lacks native durability/queue support; requires custom state store to avoid long-running processes; limited production guidance compared to SDKs.

## LangChain / LangGraph
- **Fit with provider abstraction**: Uses `BaseLanguageModel`/`ChatModel` interfaces with many providers (Azure OpenAI, OpenAI, Anthropic, local). Configuration can be centralized via environment variables or a provider mapping module.
- **Queueing model**: LangGraph provides message-passing DAGs and checkpointers. To align with Service Bus, worker processes can dequeue tasks and feed them into a graph run, while checkpointer (e.g., Postgres/Redis/Azure Table) maintains state.
- **Prototype/steps**:
  1. Build a small LangGraph with nodes for provider-backed agents; register Azure OpenAI through `AzureChatOpenAI`.
  2. Use the `AsyncLangGraph` API inside a Service Bus-triggered Function/App Service worker; supply a custom checkpointer pointing to Azure Tables or Cosmos DB.
  3. When emitting downstream work, publish queue messages containing the graph run ID and next edge payload.
- **Pros**: Mature ecosystem; graph abstraction matches orchestrator needs; extensive tooling around retrievers and memory; Python/TypeScript options.
- **Cons/Gaps**: Operational overhead for state store; licensing considerations for some integrations; queue alignment still requires glue code and retry/idempotency design.

## Prompt Flow / Azure AI Foundry Flows
- **Fit with provider abstraction**: Designed for Azure OpenAI and custom connections; supports parameterized deployments and data bindings. Less generic for non-Azure providers but matches the targeted cloud stack.
- **Queueing model**: Flow runs are executed via jobs; can be triggered through Azure ML endpoints or Functions. Queue-triggered Function can start a flow run and poll status; outputs can be written back to queues/Dataverse.
- **Prototype/steps**:
  1. Author a minimal flow (chat or tool-calling) that accepts inputs matching the agent contract.
  2. Expose the flow as an endpoint; create a Service Bus-triggered Function that calls the endpoint and posts results to the response queue.
  3. Capture run metadata (run ID, status) in Log Analytics and correlate with queue message IDs.
- **Pros**: Native Azure governance, monitoring and CI/CD via AML; visual authoring suits citizen developers; easy model observability.
- **Cons/Gaps**: Less flexible for heterogeneous providers; multi-agent orchestration is limited; vendor lock-in to Azure ML workspace.

## Recommendation Snapshot
- **Short list**: Semantic Kernel for provider abstraction + Function/Service Bus glue; LangGraph when a richer DAG/message-passing model is required; Prompt Flow for Azure-native governance and observability on standardized flows.
- **Open gaps**: Durable queue integration is not first-class in any framework; we need a shared state pattern (Table/Cosmos/SQL) plus idempotent message contract. Production runbooks for AutoGen are immature compared to SDK-backed options.
- **Next steps**: Prototype Semantic Kernel + Service Bus worker as the default path, while keeping a LangGraph spike for complex routing. Document the queue contract (message schema, correlation IDs, retry policy) alongside provider registry configuration.
