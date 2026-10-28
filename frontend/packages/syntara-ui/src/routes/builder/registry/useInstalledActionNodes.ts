import { RhUiPlugFillIcon } from '@patternfly/react-icons'
import type { PluginPlatformAPI } from '@syntara/contracts'
import { useQueries } from '@tanstack/react-query'
import { useEffect, useMemo } from 'react'

import { pluginPlatformClient, pluginPlatformFetchClient } from '../../../client'
import { useWorkflowStore } from '../../../stores/useWorkflowStore'
import { createExternalActionActivity } from '../../../stores/workflowFactories'
import {
  ExternalActionNodeForm,
  type ExternalActionFormData,
  type InstalledPluginAction,
} from '../node-forms/ExternalActionNodeForm'
import { buildNamedActivity } from '../utils/nodeCreationHelpers'

import { NodeRegistry, type NodeTypeDefinition } from './NodeRegistry'

export const INSTALLED_ACTION_NODE_PREFIX = 'installed-plugin-action:'

type InstalledPlugin = PluginPlatformAPI.components['schemas']['InstalledPluginRead']
type InstalledPluginDetail = PluginPlatformAPI.components['schemas']['InstalledPluginDetailRead']

function nodeIdFor(action: InstalledPluginAction): string {
  return `${INSTALLED_ACTION_NODE_PREFIX}${action.step_revision_id}`
}

function asInstalledDetail(response: { data?: InstalledPluginDetail; error?: unknown }): InstalledPluginDetail {
  if (!response.data) {
    throw response.error instanceof Error ? response.error : new Error('Installed plugin action could not be loaded.')
  }
  return response.data
}

function submitInstalledAction({
  action,
  pluginDisplayName,
  data,
  onSuccess,
  onError,
}: Readonly<{
  action: InstalledPluginAction
  pluginDisplayName: string
  data: ExternalActionFormData
  onSuccess: (newNodeId?: string) => void
  onError: (error: string) => void
}>): void {
  try {
    const { activityId, activity } = buildNamedActivity(
      `${pluginDisplayName} ${action.display_name}`,
      data.name,
      (id, name) =>
        createExternalActionActivity({
          id,
          name,
          stepRevisionId: action.step_revision_id,
          inputs: data.inputs,
          pluginIntegrationId: data.pluginIntegrationId,
        })
    )
    useWorkflowStore.getState().addActivity(activity)
    onSuccess(activityId)
  } catch (error) {
    onError(error instanceof Error ? error.message : 'Failed to add installed plugin action')
  }
}

function makeNodeDefinition(
  plugin: InstalledPluginDetail,
  actions: readonly InstalledPluginAction[]
): NodeTypeDefinition<ExternalActionFormData> {
  const pluginId = `installed-plugin:${plugin.id}`
  return {
    id: pluginId,
    label: plugin.display_name,
    icon: RhUiPlugFillIcon,
    category: 'action',
    description: plugin.description,
    keywords: [plugin.namespace, plugin.name, 'plugin', 'installed'],
    order: 35,
    formComponent: ExternalActionNodeForm,
    selectionTitle: `Select a step from ${plugin.display_name}`,
    subtypes: actions.map((action) => ({
      id: nodeIdFor(action),
      label: action.display_name,
      icon: RhUiPlugFillIcon,
      description: action.description,
      keywords: [action.qualified_name, 'step type', 'installed'],
      formComponent: ExternalActionNodeForm,
      formProps: { action },
    })),
    onSubmit: (data, onSuccess, onError, subtypeId) => {
      const action = actions.find((candidate) => nodeIdFor(candidate) === subtypeId)
      if (!action) {
        onError('Select a step type from the installed plugin.')
        return
      }
      submitInstalledAction({ action, pluginDisplayName: plugin.display_name, data, onSuccess, onError })
    },
  }
}

/**
 * Make active installed actions available in the existing Add step registry.
 *
 * The browser reads safe, persisted action contracts only. Each resulting
 * workflow node stores the immutable action revision, not the plugin image or
 * any registry/runtime configuration.
 */
export function useInstalledActionNodes(): readonly NodeTypeDefinition<ExternalActionFormData>[] {
  const installedQuery = pluginPlatformClient.useQuery('get', '/plugin_registry', {
    params: { query: { enabled: true, include_total: true } },
  })
  const installedPlugins = (installedQuery.data?.resources ?? []) as InstalledPlugin[]
  const details = useQueries({
    queries: installedPlugins.map((plugin) => ({
      queryKey: ['installed-plugin-actions', plugin.id, plugin.version],
      queryFn: async () => {
        const response = await pluginPlatformFetchClient.GET('/plugin_registry/{namespace}/{name}/versions/{version}', {
          params: { path: { namespace: plugin.namespace, name: plugin.name, version: plugin.version } },
        })
        return asInstalledDetail(response)
      },
      staleTime: 30_000,
    })),
  })
  const detailKey = details
    .map((detail) => detail.data?.actions.map((action) => action.step_revision_id).join(',') ?? '')
    .join('|')
  const definitions = useMemo(() => {
    return details.flatMap((detail) => {
      const plugin = detail.data
      if (!plugin?.enabled || plugin.actions.length === 0) return []
      return [makeNodeDefinition(plugin, plugin.actions)]
    })
  }, [detailKey]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const nodeIds = definitions.map((definition) => definition.id)
    definitions.forEach((definition) => NodeRegistry.register(definition))
    return () => {
      nodeIds.forEach((nodeId) => NodeRegistry.unregister(nodeId))
    }
  }, [definitions])

  return definitions
}
