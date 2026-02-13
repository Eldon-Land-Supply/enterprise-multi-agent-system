## A06 – Conversational Bot Agent (Teams/Outlook Bot)

### Summary

The Conversational Bot Agent provides a natural‑language interface for users via Microsoft Teams or Outlook.  It accepts questions or commands from users, authenticates them, and passes structured requests to the orchestrator (A02).  It also delivers responses back to users, enabling human‑in‑the‑loop interactions with the multi‑agent system.

### Responsibilities & Tasks

**Responsibilities**

- Interface with users in Teams and Outlook, handling authentication and conversation context.
- Translate user messages into structured requests and route them to A02.
- Present results or status updates from the orchestrator back to users in a friendly format.
- Manage conversation state across multiple turns when necessary.

**Concrete tasks**

1. **User request handling** – listens for messages in Teams/Outlook.  Validates user identity via Entra ID, extracts intent and context, constructs a JSON payload, and sends it to A02’s HTTP endpoint.  Displays a “processing” message to the user.
2. **Response delivery** – receives the response from A02 (or directly from A01 for ad‑hoc queries) and posts a message back to the user, formatting the content using adaptive cards or rich text.  Handles long responses by summarising or providing attachments.
3. **Follow‑up questions** – maintains context for multi‑turn conversations.  When a user asks a follow‑up question, includes previous conversation context in the request to A02 so that the system can respond coherently.
4. **Error and fallback** – if A02 returns an error or fails, informs the user gracefully and provides guidance (e.g. “Please try again later” or “Contact support”).

### Integrations & Dependencies

| Component | Purpose | Direction | Connector/Protocol |
|-----------|--------|---------|--------------------|
| **Teams / Outlook** | Receive user messages and post responses | Read & write | Bot Framework SDK / Teams connector |
| **Power Automate/Logic App (A02)** | Send structured requests and receive responses | Call | HTTP |
| **Entra ID** | Authenticate users and access conversation context | Read | OAuth / AAD authentication |
| **Dataverse** | Store conversation logs and context for auditing and follow‑ups | Write | Dataverse connector |

### Triggers, Inputs & Outputs

**Triggers**: User messages in Teams or Outlook; inbound webhooks if used with other channels.  The bot remains idle until a message arrives.

**Inputs**: Natural‑language text from the user; conversation metadata (e.g. user ID, channel); context of previous interactions.  The bot uses language understanding (LUIS or Azure Cognitive Services) to extract intent.

**Outputs**: Structured JSON requests to A02; formatted messages back to the user; optional attachments such as PDFs or links to Dataverse records.

### Error Handling & Resilience

If A02 fails to respond within a timeout, the bot informs the user and retries once.  It handles unexpected input by asking clarifying questions.  Conversation state is stored in Dataverse so that restarted bot instances can resume where they left off.  Rate limits and throttling are managed by the Bot Framework.

### Security & Access

The bot uses a registered application in Entra ID and enforces single‑sign‑on.  It ensures that each user’s request is authorised before forwarding it.  Sensitive information is masked in conversation logs.  Access to Dataverse and A02’s endpoint is scoped to the bot’s managed identity.  Messages are transmitted over TLS.
