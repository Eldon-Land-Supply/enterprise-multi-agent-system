# Production webhook setup: GitHub, OpenAI, Claude, and OneDrive

This runbook deploys the repository's Azure Functions gateway and registers the
four provider integrations. The account owner must supply Azure, OpenAI,
Anthropic, and Microsoft 365 credentials; no source-controlled script invents or
stores them.

The consumer ChatGPT and Claude websites are not generic webhook destinations.
This gateway integrates an OpenAI API project, the Anthropic Messages API,
Anthropic Managed Agents webhooks, GitHub repository webhooks, and Microsoft
Graph change notifications.

## What the gateway guarantees

- Provider signatures or Microsoft Graph `clientState` are checked before work.
- Unresolved Key Vault reference literals fail closed.
- GitHub uses stable repository-ID admission, safe event/action/actor rules, and
  an atomic per-repository daily model-call quota. Only `push` is enabled by
  default.
- GitHub, OpenAI, Anthropic, and generic input is staged in a Table/Blob
  inbox-outbox before queue delivery.
- Validated OneDrive batches publish one small dirty signal directly to Service
  Bus within Microsoft Graph's three-second budget; duplicate signals are safe
  because the worker reads the durable delta cursor under a distributed lease.
- Private Blob claim checks carry large envelopes with SHA-256 verification.
- OpenAI and Claude inputs are sanitized and bounded; outputs use strict schemas,
  fixed output limits, 45-second request timeouts, and zero SDK retries.
- In `both` mode, each provider result is checkpointed independently.
- A separate first-wins result outbox prevents an ambiguous result send from
  repeating paid model calls.
- OneDrive notification batches collapse into one dirty signal, then delta sync
  uses a distributed lease and owner-conditional cursor commit.
- OneDrive subscription creation, renewal, delta seeding, and reauthorization all
  use the Function's managed identity.

The two Service Bus queues use seven-day duplicate detection. The Function has a
ten-minute execution budget, eleven-minute broker lock renewal, and a twelve-minute
OneDrive lease. Claim-check blobs are not age-deleted independently of the outbox
or dead-letter queues; clean them only after terminal settlement.

## 1. Prerequisites

- Azure CLI, PowerShell 7+, Python 3.11, and Bicep.
- An Azure subscription and resource group.
- An infrastructure operator with resource deployment rights **and**
  `Microsoft.Authorization/roleAssignments/write`: Owner, or Contributor plus
  Role Based Access Control Administrator/User Access Administrator.
- A GitHub OIDC application/service principal and a protected GitHub environment
  named `production`.
- An OpenAI API project, API key, approved model ID, and webhook-management access.
- An Anthropic workspace, API key, approved structured-output model ID, and
  Managed Agents webhook access when those events are used.
- A Microsoft 365 work/school tenant, a OneDrive for Business drive, and a tenant
  administrator who can grant Microsoft Graph application permissions. The Azure
  subscription and Function managed identity must be in that same Entra tenant.

Use distinct development and production projects. Never commit tokens, signing
secrets, Function keys, Graph tokens, or OneDrive download URLs.

## 2. Collect stable identifiers

The target GitHub repository's stable numeric ID is `1116614709`. Keep using the
numeric ID even if the repository is renamed.

Discover the OneDrive for Business drive with the connected OneDrive profile,
Graph Explorer, or an authorized call to:

```text
GET https://graph.microsoft.com/v1.0/users/{user-id}/drive?$select=id
```

Create the GitHub OIDC Entra application/service principal and federated
credential first. The credential subject is:

```text
repo:Eldonlandsupply/enterprise-multi-agent-system:environment:production
```

Resolve its **object ID**, not its client/application ID:

```powershell
$deploymentClientId = '<GitHub OIDC application client ID>'
$deploymentPrincipalObjectId = az ad sp show `
  --id $deploymentClientId `
  --query id -o tsv
```

## 3. Validate and deploy Azure infrastructure

```powershell
$resourceGroup = '<resource-group>'
$location = '<azure-region>'
$openAIModel = '<approved-openai-model-id>'
$anthropicModel = '<approved-anthropic-model-id>'
$driveId = '<business-onedrive-drive-id>'
$githubRepositoryId = '1116614709'

az bicep build --file infra/bicep/webhook-gateway.bicep
az group create --name $resourceGroup --location $location

az deployment group validate `
  --resource-group $resourceGroup `
  --template-file infra/bicep/webhook-gateway.bicep `
  --parameters `
    aiProvider='both' `
    openAIModel=$openAIModel `
    anthropicModel=$anthropicModel `
    onedriveDriveId=$driveId `
    githubAllowedRepositoryIds=$githubRepositoryId `
    deploymentPrincipalObjectId=$deploymentPrincipalObjectId

$deployment = az deployment group create `
  --resource-group $resourceGroup `
  --template-file infra/bicep/webhook-gateway.bicep `
  --parameters `
    aiProvider='both' `
    openAIModel=$openAIModel `
    anthropicModel=$anthropicModel `
    onedriveDriveId=$driveId `
    githubAllowedRepositoryIds=$githubRepositoryId `
    deploymentPrincipalObjectId=$deploymentPrincipalObjectId | ConvertFrom-Json

$functionAppName = $deployment.properties.outputs.functionAppName.value
$functionPrincipalId = $deployment.properties.outputs.functionPrincipalId.value
$baseUrl = $deployment.properties.outputs.baseUrl.value
$keyVaultName = $deployment.properties.outputs.keyVaultName.value
$storageAccountName = $deployment.properties.outputs.storageAccountName.value
$serviceBusNamespace = $deployment.properties.outputs.serviceBusNamespace.value
```

The template grants the Function identity Storage Blob/Table/Queue, Service Bus,
and Key Vault access. It grants the GitHub OIDC identity Website Contributor on
the Function and Storage Blob Data Contributor on the storage account, which the
Linux Consumption deployment path needs to upload its external package. Bicep
owns static app settings but preserves the current `WEBSITE_RUN_FROM_PACKAGE`
value written by the code deployment.

## 4. Store secrets in Key Vault

The operator needs Key Vault Secrets Officer; the Function receives read-only
Secrets User access.

```powershell
$vaultId = az keyvault show --name $keyVaultName --query id -o tsv
$operatorId = az ad signed-in-user show --query id -o tsv
az role assignment create `
  --assignee-object-id $operatorId `
  --assignee-principal-type User `
  --role 'Key Vault Secrets Officer' `
  --scope $vaultId

function New-WebhookSecret {
  [Convert]::ToBase64String(
    [Security.Cryptography.RandomNumberGenerator]::GetBytes(48)
  )
}

$githubWebhookSecret = New-WebhookSecret
$inboundWebhookSecret = New-WebhookSecret
$oneDriveClientState = New-WebhookSecret

az keyvault secret set --vault-name $keyVaultName --name github-webhook-secret --value $githubWebhookSecret
az keyvault secret set --vault-name $keyVaultName --name inbound-webhook-secret --value $inboundWebhookSecret
az keyvault secret set --vault-name $keyVaultName --name onedrive-client-state --value $oneDriveClientState
az keyvault secret set --vault-name $keyVaultName --name openai-api-key --value '<OpenAI API key>'
az keyvault secret set --vault-name $keyVaultName --name anthropic-api-key --value '<Anthropic API key>'
```

Keep the three generated values only long enough to register providers, then
remove them from the shell.

## 5. Configure and run the deployment workflow

Create the protected GitHub environment `production` with:

- secrets `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, and `AZURE_SUBSCRIPTION_ID`;
- variable `AZURE_FUNCTION_APP_NAME` set to `$functionAppName`; and
- required reviewers appropriate for production.

Run **Deploy webhook gateway** from `main`. An unprivileged build job installs
hash-locked wheel dependencies, runs tests, compiles Bicep, and uploads the
immutable package. Only the separate production job can request an OIDC token, and it
deploys exactly that reviewed artifact.

## 6. Grant Microsoft Graph and bootstrap OneDrive

For OneDrive for Business subscription GET/PATCH/renewal, grant the Function
managed identity the Microsoft Graph application role `Files.ReadWrite.All`.
This role also covers the delta reads used by the gateway.

```powershell
$graphAppId = '00000003-0000-0000-c000-000000000000'
$graphSp = az ad sp show --id $graphAppId | ConvertFrom-Json
$filesReadWriteAll = $graphSp.appRoles | Where-Object {
  $_.value -eq 'Files.ReadWrite.All' -and
  $_.allowedMemberTypes -contains 'Application'
}
if (-not $filesReadWriteAll) { throw 'Files.ReadWrite.All app role was not found' }

$assignment = @{
  principalId = $functionPrincipalId
  resourceId = $graphSp.id
  appRoleId = $filesReadWriteAll.id
} | ConvertTo-Json -Compress

az rest `
  --method POST `
  --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$($graphSp.id)/appRoleAssignedTo" `
  --headers 'Content-Type=application/json' `
  --body $assignment
```

After admin consent propagates, call the Function-key-protected bootstrap. The
Function managed identity seeds `delta?token=latest`, reuses a matching
subscription if one exists, otherwise creates it, and stores the ID/expiry in
Azure Table. No local Azure CLI identity creates Graph subscription state.

```powershell
$functionKey = az functionapp keys list `
  --resource-group $resourceGroup `
  --name $functionAppName `
  --query functionKeys.default -o tsv

$env:WEBHOOK_GATEWAY_FUNCTION_KEY = $functionKey
python scripts/configure_onedrive_subscription.py --base-url $baseUrl
Remove-Item Env:WEBHOOK_GATEWAY_FUNCTION_KEY
```

The twice-daily timer reads the durable subscription record, renews it with
`PATCH` when fewer than seven days remain, and enqueues a reconciliation delta.
A `reauthorizationRequired` lifecycle signal uses the same PATCH renewal path.

## 7. Register the GitHub webhook

The helper defaults to `push` only. That matters because this repository is
public: comments and fork pull requests authenticate as genuine GitHub traffic,
but their authors are not automatically authorized to spend your model budget.

Create a temporary fine-grained token restricted to this repository with
**Webhooks: Read and write**:

```powershell
$tokenSecure = Read-Host 'Fine-grained GitHub token' -AsSecureString
$env:GITHUB_TOKEN = [Net.NetworkCredential]::new('', $tokenSecure).Password
$env:GITHUB_WEBHOOK_SECRET = $githubWebhookSecret

python scripts/configure_github_webhook.py `
  --repo 'Eldonlandsupply/enterprise-multi-agent-system' `
  --url "$baseUrl/webhooks/github"

Remove-Item Env:GITHUB_TOKEN
Remove-Item Env:GITHUB_WEBHOOK_SECRET
```

Revoke the temporary token. A valid delivery returns `200`; invalid signatures
return `401`. The default daily quota is 100 unique accepted deliveries for this
repository, and retries of one delivery consume one unit.

To opt in to another event, do both steps deliberately:

1. add repeated `--event` arguments to the helper; and
2. update `GITHUB_ALLOWED_EVENTS` in IaC. Pull-request, issue, and comment events
   additionally require `OWNER`, `MEMBER`, or `COLLABORATOR`; check events require
   an explicitly allowed GitHub App ID.

### GitHub secret rotation without downtime

1. Store the old value as `github-webhook-previous-secret`.
2. Write a new version of `github-webhook-secret`.
3. Refresh App Service Key Vault references.
4. Run the helper with the new secret.
5. Confirm a delivery, then delete the previous secret after the retry window.

The Bicep template preserves the previous-secret reference, and the verifier
ignores it when the Key Vault secret does not exist.

## 8. Register OpenAI Responses webhooks

In the OpenAI API project dashboard, create:

- URL: `$baseUrl/webhooks/openai`
- events: `response.completed`, `response.failed`, `response.cancelled`, and
  `response.incomplete`

Store the one-time signing secret:

```powershell
az keyvault secret set `
  --vault-name $keyVaultName `
  --name openai-webhook-secret `
  --value '<OpenAI dashboard webhook signing secret>'
```

The receiver verifies the unmodified body with the official SDK and deduplicates
`webhook-id`. Completed-response retrieval is disabled by default so a shared
OpenAI project cannot export another application's response. Enable
`OPENAI_CALLBACK_RETRIEVAL=true` only for a dedicated project owned by this
gateway; even then only `id`, `status`, `model`, and `output_text` are emitted.

## 9. Register Claude

Set `AI_PROVIDER=claude` or `both` for Messages API analysis. The worker requests
strict JSON-schema output and checkpoints each provider separately.

For Anthropic Managed Agents, open **Manage -> Webhooks** in the Anthropic Console:

- URL: `$baseUrl/webhooks/anthropic`
- events: only the session/thread/vault/agent/deployment events your workflow uses

Store the one-time `whsec_` signing key:

```powershell
az keyvault secret set `
  --vault-name $keyVaultName `
  --name anthropic-webhook-signing-key `
  --value '<whsec-signing-key>'
```

The runtime dependency is `anthropic[webhooks]`, which installs the official
standard-webhook verifier used by `client.beta.webhooks.unwrap(...)`.

## 10. Verify and operate

Refresh Key Vault references or restart the Function after all secrets exist:

```powershell
Invoke-RestMethod "$baseUrl/health"
```

Expected status is `ready`. A `503 configuration_required` identifies only
missing categories and never returns a secret.

Verify:

- GitHub, OpenAI, and Anthropic test deliveries return `2xx`.
- public GitHub actors/events are acknowledged with `ignored=1`, not enqueued.
- the daily GitHub quota stops new events at the configured boundary.
- OneDrive validation echoes the already-decoded token exactly; normal batches
  return `202` and log `elapsed_ms` (alert if acknowledgements exceed two seconds).
- both Table outboxes have no long-lived pending rows.
- both Service Bus dead-letter queues are empty or archived within the recovery SLA.
- OneDrive renewal succeeds before the seven-day threshold.
- result consumers are idempotent and resolve private claim checks.
- logs contain event/correlation IDs, not bodies, prompts, secrets, document
  content, SAS URLs, or Graph download URLs.

Create Azure Monitor alerts for:

- nonzero `DeadletteredMessages` on both queues;
- Function failures/exceptions;
- result/intake outbox rows pending beyond the chosen SLA;
- OneDrive webhook `elapsed_ms > 2000`; and
- subscription expiration inside seven days.

Do not delete claim-check blobs merely because they are old. Cleanup is safe only
after the corresponding outbox record is `sent`, the relevant dead-letter queue
has been reconciled, and the replay retention period has passed.

Useful official references:

- [OpenAI webhooks](https://platform.openai.com/docs/guides/webhooks)
- [Anthropic Managed Agents webhooks](https://platform.claude.com/docs/en/managed-agents/webhooks)
- [GitHub webhook events and payloads](https://docs.github.com/en/webhooks/webhook-events-and-payloads)
- [Microsoft Graph change notifications](https://learn.microsoft.com/en-us/graph/change-notifications-overview)
- [Microsoft Graph lifecycle notifications](https://learn.microsoft.com/en-us/graph/change-notifications-lifecycle-events)
- [Azure Functions run from package](https://learn.microsoft.com/en-us/azure/azure-functions/run-functions-from-deployment-package)

The environment owner must still supply the real tenant, drive, models, API keys,
signing secrets, Function key, Entra admin consent, and production hostname.
