import type { PluginPlatformAPI } from '@syntara/contracts'
import { useQueries } from '@tanstack/react-query'

import { pluginPlatformClient, pluginPlatformFetchClient } from '../../../client'

type InstalledPlugin = PluginPlatformAPI.components['schemas']['InstalledPluginRead']
type InstalledPluginDetail = PluginPlatformAPI.components['schemas']['InstalledPluginDetailRead']
type IntegrationType = PluginPlatformAPI.components['schemas']['InstalledPluginIntegrationTypeRead']
type CredentialType = PluginPlatformAPI.components['schemas']['InstalledPluginCredentialTypeRead']

export type PluginIntegrationContract = Readonly<{
  integrationType: IntegrationType
  credentialTypes: readonly CredentialType[]
  sourcePlugin: Readonly<{
    namespace: string
    name: string
    version: string
    displayName: string
  }>
}>

function contractsFrom(details: readonly InstalledPluginDetail[]): readonly PluginIntegrationContract[] {
  return details.flatMap((detail) =>
    (detail.integration_types ?? []).map((integrationType) => ({
      integrationType,
      credentialTypes: (detail.credential_types ?? []).filter(
        (credentialType) => credentialType.integration_type_revision_id === integrationType.id
      ),
      sourcePlugin: {
        namespace: detail.namespace,
        name: detail.name,
        version: detail.version,
        displayName: detail.display_name,
      },
    }))
  )
}

/**
 * Loads installed plugin contracts once and preserves the plugin that supplied
 * each type. UI surfaces use this source label so admins can distinguish a
 * platform adapter from a declarative plugin-contributed integration.
 */
export function useInstalledPluginIntegrationContracts() {
  const installedQuery = pluginPlatformClient.useQuery('get', '/plugin_registry', {
    params: { query: { enabled: true, include_total: true } },
  })
  const installedPlugins = installedQuery.data?.resources ?? []
  const detailQueries = useQueries({
    queries: installedPlugins.map((plugin: InstalledPlugin) => ({
      queryKey: ['installed-plugin-integration-types', plugin.id, plugin.version],
      queryFn: async () => {
        const response = await pluginPlatformFetchClient.GET('/plugin_registry/{namespace}/{name}/versions/{version}', {
          params: { path: { namespace: plugin.namespace, name: plugin.name, version: plugin.version } },
        })
        if (!response.data) {
          throw response.error instanceof Error
            ? response.error
            : new Error('Installed plugin integration types could not be loaded.')
        }
        return response.data
      },
      staleTime: 30_000,
    })),
  })
  const details = detailQueries.flatMap((query) => (query.data ? [query.data] : []))

  return {
    contracts: contractsFrom(details),
    isPending: installedQuery.isPending || detailQueries.some((query) => query.isPending),
    error: installedQuery.error ?? detailQueries.find((query) => query.error)?.error,
    refetch: installedQuery.refetch,
  }
}
