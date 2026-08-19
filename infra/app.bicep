// Stage 2 — the application itself, once its image exists in the registry.

targetScope = 'resourceGroup'

param appName string = 'capacityintelligence'

@allowed(['dev', 'test', 'prod'])
param env string = 'dev'

param location string = resourceGroup().location

@description('Image tag to run. The pipeline passes the build id so every deploy is traceable to a commit.')
param imageTag string

@description('Login credentials as comma-separated user:salt:hash entries.')
@secure()
param appUsers string

@description('Key used to sign session cookies.')
@secure()
param appSecretKey string

// Managed-identity pull is the better answer, and the template still supports
// it. It is off by default because granting the app AcrPull is a role
// assignment, and Contributor excludes Microsoft.Authorization/*/Write --
// so this template cannot grant it. Once someone with Owner has, set this
// true and turn adminUserEnabled off in platform.bicep.
param useManagedIdentityPull bool = false

var acrName = 'acr${appName}${env}'
var caeName = 'cae-${appName}-${env}'
var containerAppName = 'ca-${appName}-${env}'

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrName
}

resource cae 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: caeName
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: containerAppName
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: cae.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      registries: useManagedIdentityPull ? [
        {
          server: acr.properties.loginServer
          identity: 'system'
        }
      ] : [
        {
          server: acr.properties.loginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: useManagedIdentityPull ? [
        { name: 'app-users', value: appUsers }
        { name: 'app-secret-key', value: appSecretKey }
      ] : [
        { name: 'app-users', value: appUsers }
        { name: 'app-secret-key', value: appSecretKey }
        { name: 'acr-password', value: acr.listCredentials().passwords[0].value }
      ]
    }
    template: {
      containers: [
        {
          name: appName
          image: '${acr.properties.loginServer}/${appName}:${imageTag}'
          resources: {
            // 0.5 CPU left the startup warm competing with the first request and
            // a cold /api/overview took 144 seconds. The build is single-threaded
            // and CPU-bound, so a whole core roughly halves it.
            cpu: json('1.0')
            memory: '2Gi'
          }
          env: [
            { name: 'APP_USERS', secretRef: 'app-users' }
            { name: 'APP_SECRET_KEY', secretRef: 'app-secret-key' }
            // TLS terminates at the ingress, so the session cookie must be
            // marked secure or a browser will refuse to send it back.
            { name: 'COOKIE_SECURE', value: '1' }
            { name: 'PORT', value: '8000' }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/health', port: 8000 }
              initialDelaySeconds: 40
              periodSeconds: 30
            }
            {
              type: 'Readiness'
              httpGet: { path: '/health', port: 8000 }
              initialDelaySeconds: 20
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        // Never scale to zero. The ontology and every forecast are built at
        // startup and cached in the process, so a cold start costs seconds --
        // and on a shared link the person paying that cost is the reviewer.
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (useManagedIdentityPull) {
  scope: acr
  name: guid(acr.id, app.id, 'AcrPull')
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '7f951dda-4ed3-4680-a7ca-43fe172d538d' // AcrPull
    )
    principalId: app.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output appUrl string = 'https://${app.properties.configuration.ingress.fqdn}'
output containerAppName string = app.name
