// Stage 1 — the platform the application lands on.
//
// Split from the app deliberately. The container app references an image by
// tag, and on a first run that image does not exist yet: the registry has to be
// created, the image pushed, and only then can the app be deployed. One
// template would either fail on first deploy or need a placeholder image
// standing in, which is a lie the pipeline then has to remember to correct.

targetScope = 'resourceGroup'

param appName string = 'capacityintelligence'

@allowed(['dev', 'test', 'prod'])
param env string = 'dev'

param location string = resourceGroup().location

var acrName = 'acr${appName}${env}'
var caeName = 'cae-${appName}-${env}'
var lawName = 'law-${appName}-${env}'

resource law 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: lawName
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: { name: 'Basic' }
  properties: {
    // Managed-identity pull is the right answer and app.bicep still supports it,
    // but granting the app AcrPull is a role assignment and Contributor excludes
    // Microsoft.Authorization/*/Write. Until someone with Owner grants that role,
    // the app authenticates with the admin credential instead. Set this false and
    // useManagedIdentityPull=true on app.bicep once the role exists.
    adminUserEnabled: true
  }
}

resource cae 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: caeName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: law.properties.customerId
        sharedKey: law.listKeys().primarySharedKey
      }
    }
  }
}

output acrName string = acr.name
output acrLoginServer string = acr.properties.loginServer
output caeId string = cae.id
