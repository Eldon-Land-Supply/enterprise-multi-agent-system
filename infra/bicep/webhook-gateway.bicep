@description('Short lowercase prefix used for globally unique resource names.')
param prefix string = 'emawebhook'

@description('Azure region for all resources.')
param location string = resourceGroup().location

@allowed([
  'openai'
  'claude'
  'both'
])
param aiProvider string

@description('Approved OpenAI model identifier.')
@minLength(1)
param openAIModel string

@description('Approved Anthropic structured-output model identifier.')
@minLength(1)
param anthropicModel string

@description('OneDrive for Business drive ID discovered before deployment.')
@minLength(1)
param onedriveDriveId string

@description('Comma-separated numeric GitHub repository IDs allowed to trigger the gateway.')
@minLength(1)
param githubAllowedRepositoryIds string

@description('Object ID of the GitHub OIDC service principal used for code deployment.')
@minLength(1)
param deploymentPrincipalObjectId string

var suffix = uniqueString(resourceGroup().id)
var compactPrefix = toLower(replace(prefix, '-', ''))
var storageName = take('${compactPrefix}${suffix}', 24)
var serviceBusName = take('${prefix}-${suffix}', 50)
var functionName = take('${prefix}-${suffix}', 60)
var vaultName = take('${prefix}-kv-${suffix}', 24)
var insightsName = '${prefix}-insights'
var eventQueueName = 'ai-events'
var resultQueueName = 'ai-results'
var payloadContainerName = 'webhook-payloads'

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    deleteRetentionPolicy: {
      enabled: true
      days: 7
    }
    containerDeleteRetentionPolicy: {
      enabled: true
      days: 7
    }
  }
}

resource payloadContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: payloadContainerName
  properties: {
    publicAccess: 'None'
  }
}

resource tableService 'Microsoft.Storage/storageAccounts/tableServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource webhookDeliveriesTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: 'WebhookDeliveries'
}

resource webhookResultsTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: 'WebhookResults'
}

resource githubQuotaTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: 'GitHubDailyModelQuota'
}

resource onedriveSubscriptionsTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: 'OneDriveSubscriptions'
}

resource onedriveDeltaCursorsTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: 'OneDriveDeltaCursors'
}

resource serviceBus 'Microsoft.ServiceBus/namespaces@2024-01-01' = {
  name: serviceBusName
  location: location
  sku: {
    name: 'Standard'
    tier: 'Standard'
  }
  properties: {
    disableLocalAuth: true
    minimumTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
  }
}

resource eventQueue 'Microsoft.ServiceBus/namespaces/queues@2024-01-01' = {
  parent: serviceBus
  name: eventQueueName
  properties: {
    requiresDuplicateDetection: true
    duplicateDetectionHistoryTimeWindow: 'P7D'
    lockDuration: 'PT1M'
    maxDeliveryCount: 10
    deadLetteringOnMessageExpiration: true
    defaultMessageTimeToLive: 'P14D'
  }
}

resource resultQueue 'Microsoft.ServiceBus/namespaces/queues@2024-01-01' = {
  parent: serviceBus
  name: resultQueueName
  properties: {
    requiresDuplicateDetection: true
    duplicateDetectionHistoryTimeWindow: 'P7D'
    lockDuration: 'PT1M'
    maxDeliveryCount: 10
    deadLetteringOnMessageExpiration: true
    defaultMessageTimeToLive: 'P14D'
  }
}

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: insightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
  }
}

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${prefix}-plan'
  location: location
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
  }
  properties: {
    reserved: true
  }
}

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: vaultName
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enablePurgeProtection: true
    softDeleteRetentionInDays: 90
    publicNetworkAccess: 'Enabled'
  }
}

resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: functionName
  location: location
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    clientAffinityEnabled: false
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      alwaysOn: false
    }
  }
  dependsOn: [
    eventQueue
    resultQueue
    payloadContainer
    webhookDeliveriesTable
    webhookResultsTable
    githubQuotaTable
    onedriveSubscriptionsTable
    onedriveDeltaCursorsTable
  ]
}

var staticAppSettings = {
  FUNCTIONS_EXTENSION_VERSION: '~4'
  FUNCTIONS_WORKER_RUNTIME: 'python'
  AzureWebJobsStorage__accountName: storage.name
  APPLICATIONINSIGHTS_CONNECTION_STRING: insights.properties.ConnectionString
  APP_ENV: 'production'
  PUBLIC_BASE_URL: 'https://${functionApp.properties.defaultHostName}/api'
  SERVICE_BUS_CONNECTION__fullyQualifiedNamespace: '${serviceBus.name}.servicebus.windows.net'
  SERVICE_BUS_FULLY_QUALIFIED_NAMESPACE: '${serviceBus.name}.servicebus.windows.net'
  SERVICE_BUS_QUEUE_NAME: eventQueueName
  SERVICE_BUS_RESULT_QUEUE_NAME: resultQueueName
  IDEMPOTENCY_BACKEND: 'azure_table'
  IDEMPOTENCY_STORAGE_ACCOUNT_URL: 'https://${storage.name}.table.${environment().suffixes.storage}'
  IDEMPOTENCY_TABLE_NAME: 'WebhookDeliveries'
  RESULT_OUTBOX_TABLE_NAME: 'WebhookResults'
  GITHUB_QUOTA_TABLE_NAME: 'GitHubDailyModelQuota'
  ONEDRIVE_SUBSCRIPTION_TABLE_NAME: 'OneDriveSubscriptions'
  PAYLOAD_STORAGE_ACCOUNT_URL: 'https://${storage.name}.blob.${environment().suffixes.storage}'
  PAYLOAD_BLOB_CONTAINER: payloadContainerName
  MAX_WEBHOOK_BYTES: '1048576'
  GITHUB_MAX_WEBHOOK_BYTES: '26214400'
  MAX_QUEUE_MESSAGE_BYTES: '192000'
  GITHUB_ALLOWED_REPOSITORY_IDS: githubAllowedRepositoryIds
  GITHUB_ALLOWED_EVENTS: 'push'
  GITHUB_TRUSTED_AUTHOR_ASSOCIATIONS: 'OWNER,MEMBER,COLLABORATOR'
  GITHUB_DAILY_MODEL_LIMIT: '100'
  AI_PROVIDER: aiProvider
  OPENAI_MODEL: openAIModel
  ANTHROPIC_MODEL: anthropicModel
  OPENAI_CALLBACK_RETRIEVAL: 'false'
  ONEDRIVE_DRIVE_ID: onedriveDriveId
  ONEDRIVE_TENANT_ID: subscription().tenantId
  GITHUB_WEBHOOK_SECRET: '@Microsoft.KeyVault(VaultName=${vault.name};SecretName=github-webhook-secret)'
  GITHUB_WEBHOOK_PREVIOUS_SECRET: '@Microsoft.KeyVault(VaultName=${vault.name};SecretName=github-webhook-previous-secret)'
  INBOUND_WEBHOOK_SECRET: '@Microsoft.KeyVault(VaultName=${vault.name};SecretName=inbound-webhook-secret)'
  OPENAI_WEBHOOK_SECRET: '@Microsoft.KeyVault(VaultName=${vault.name};SecretName=openai-webhook-secret)'
  ANTHROPIC_WEBHOOK_SIGNING_KEY: '@Microsoft.KeyVault(VaultName=${vault.name};SecretName=anthropic-webhook-signing-key)'
  OPENAI_API_KEY: '@Microsoft.KeyVault(VaultName=${vault.name};SecretName=openai-api-key)'
  ANTHROPIC_API_KEY: '@Microsoft.KeyVault(VaultName=${vault.name};SecretName=anthropic-api-key)'
  ONEDRIVE_CLIENT_STATE: '@Microsoft.KeyVault(VaultName=${vault.name};SecretName=onedrive-client-state)'
}

// functions-action owns this one dynamic setting on Linux Consumption. Preserve
// its current package URL while Bicep replaces every IaC-owned static setting.
var currentAppSettings = list('${functionApp.id}/config/appsettings', '2023-12-01').properties
var packageSetting = contains(currentAppSettings, 'WEBSITE_RUN_FROM_PACKAGE') ? {
  WEBSITE_RUN_FROM_PACKAGE: currentAppSettings.WEBSITE_RUN_FROM_PACKAGE
} : {}

resource functionAppSettings 'Microsoft.Web/sites/config@2023-12-01' = {
  parent: functionApp
  name: 'appsettings'
  properties: union(packageSetting, staticAppSettings)
}

resource serviceBusSenderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBus.id, functionApp.id, 'sender')
  scope: serviceBus
  properties: {
    principalId: functionApp.identity.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39')
    principalType: 'ServicePrincipal'
  }
}

resource serviceBusReceiverRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBus.id, functionApp.id, 'receiver')
  scope: serviceBus
  properties: {
    principalId: functionApp.identity.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0')
    principalType: 'ServicePrincipal'
  }
}

resource keyVaultSecretsRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, functionApp.id, 'secrets-user')
  scope: vault
  properties: {
    principalId: functionApp.identity.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
    principalType: 'ServicePrincipal'
  }
}

resource storageBlobOwnerRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, functionApp.id, 'blob-owner')
  scope: storage
  properties: {
    principalId: functionApp.identity.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
    principalType: 'ServicePrincipal'
  }
}

resource storageQueueContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, functionApp.id, 'queue-contributor')
  scope: storage
  properties: {
    principalId: functionApp.identity.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '974c5e8b-45b9-4653-ba55-5f855dd0fb88')
    principalType: 'ServicePrincipal'
  }
}

resource storageTableContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, functionApp.id, 'table-contributor')
  scope: storage
  properties: {
    principalId: functionApp.identity.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3')
    principalType: 'ServicePrincipal'
  }
}

resource deploymentWebsiteRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(functionApp.id, deploymentPrincipalObjectId, 'website-contributor')
  scope: functionApp
  properties: {
    principalId: deploymentPrincipalObjectId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'de139f84-1756-47ae-9be6-808fbbe84772')
    principalType: 'ServicePrincipal'
  }
}

resource deploymentBlobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, deploymentPrincipalObjectId, 'deployment-blob-contributor')
  scope: storage
  properties: {
    principalId: deploymentPrincipalObjectId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalType: 'ServicePrincipal'
  }
}

output functionAppName string = functionApp.name
output functionPrincipalId string = functionApp.identity.principalId
output baseUrl string = 'https://${functionApp.properties.defaultHostName}/api'
output keyVaultName string = vault.name
output serviceBusNamespace string = serviceBus.name
output storageAccountName string = storage.name
