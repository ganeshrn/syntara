import { Content, FormGroup, StackItem, Switch, Title } from '@patternfly/react-core'
import { RhUiTrashIcon } from '@patternfly/react-icons'
import { Tbody, Td, Th, Thead, Tr } from '@patternfly/react-table'
import type { PluginPlatformAPI } from '@syntara/contracts'
import { useState } from 'react'

import { pluginPlatformClient } from '../../../client'
import { SynConfirmationDialog } from '../../../components/dialogs/SynConfirmationDialog'
import { IconLabel } from '../../../components/IconLabel'
import {
  SynListPanel,
  SynListPanelTable,
  SynListPanelToolbar,
  SynListPanelView,
} from '../../../components/panels/list/SynListPanel'
import type { KebabAction } from '../../../components/SynKebabMenu'
import { SynKebabMenu } from '../../../components/SynKebabMenu'
import { DateCell } from '../../../components/table/DateCell'
import { useMutationErrorHandler } from '../../../hooks/useMutationErrorHandler'
import { useProjectSelector } from '../../../hooks/useProjectSelector'
import { useAlerts } from '../../../providers/alerts'
import type { FilterConfig } from '../../../types/filters'
import { detachPromise } from '../../../utils/detachPromise'
import type { PluginIntegrationContract } from '../plugin-integrations/useInstalledPluginIntegrationContracts'

import { getIntegrationNameFilterDefinition } from './integrationFilters'

type PluginConnection = PluginPlatformAPI.components['schemas']['PluginConnectionRead']

function displayEndpointKind(endpointKind: string) {
  return endpointKind.replace(/-/g, ' ')
}

function getEndpointOrigin(connection: PluginConnection) {
  const endpointOrigin = connection.configuration.endpointOrigin
  return typeof endpointOrigin === 'string' && endpointOrigin.trim() ? endpointOrigin : '—'
}

function getPluginLabel(contract: PluginIntegrationContract | undefined) {
  return contract ? `${contract.sourcePlugin.displayName} ${contract.sourcePlugin.version}` : 'Unknown plugin'
}

function pluginConnectionActions(
  connection: PluginConnection,
  onDelete: (connection: PluginConnection) => void,
  isPending: boolean
): KebabAction[] {
  return [
    {
      key: 'delete',
      title: <IconLabel icon={<RhUiTrashIcon />}>Delete integration</IconLabel>,
      isDanger: true,
      isAriaDisabled: isPending,
      onClick: () => onDelete(connection),
    },
  ]
}

function PluginIntegrationTable({
  connections,
  contracts,
  onToggleEnabled,
  onDelete,
  isPending,
}: Readonly<{
  connections: readonly PluginConnection[]
  contracts: readonly PluginIntegrationContract[]
  onToggleEnabled: (connection: PluginConnection) => void
  onDelete: (connection: PluginConnection) => void
  isPending: boolean
}>) {
  const contractById = new Map(contracts.map((contract) => [contract.integrationType.id, contract]))

  return (
    <>
      <Thead>
        <Tr>
          <Th>Name</Th>
          <Th>Integration type</Th>
          <Th>Source plugin</Th>
          <Th>Endpoint kind</Th>
          <Th>Endpoint origin</Th>
          <Th>Last updated</Th>
          <Th>State</Th>
          <Th screenReaderText="Actions" />
        </Tr>
      </Thead>
      <Tbody>
        {connections.map((connection) => {
          const contract = contractById.get(connection.integration_type_revision_id)
          return (
            <Tr key={connection.id}>
              <Td dataLabel="Name">{connection.name}</Td>
              <Td dataLabel="Integration type">{contract?.integrationType.display_name ?? 'Plugin integration'}</Td>
              <Td dataLabel="Source plugin">{getPluginLabel(contract)}</Td>
              <Td dataLabel="Endpoint kind">{displayEndpointKind(connection.endpoint_kind)}</Td>
              <Td dataLabel="Endpoint origin">{getEndpointOrigin(connection)}</Td>
              <Td dataLabel="Last updated">
                <DateCell dateString={connection.updated_at} />
              </Td>
              <Td dataLabel="State">
                <Switch
                  id={`plugin-connection-toggle-${connection.id}`}
                  label={connection.enabled ? 'Enabled' : 'Disabled'}
                  isChecked={connection.enabled}
                  isDisabled={isPending}
                  onChange={() => onToggleEnabled(connection)}
                  aria-label={`Toggle ${connection.name}`}
                />
              </Td>
              <Td isActionCell>
                <SynKebabMenu
                  aria-label={`Actions for ${connection.name}`}
                  actions={pluginConnectionActions(connection, onDelete, isPending)}
                />
              </Td>
            </Tr>
          )
        })}
      </Tbody>
    </>
  )
}

/**
 * Project-scoped instances of declarative plugin contracts.
 *
 * A table gives operators lifecycle controls and safe non-secret context while
 * keeping plugin connections distinct from adapter-backed integrations.
 */
export function PluginIntegrationsList({ contracts }: Readonly<{ contracts: readonly PluginIntegrationContract[] }>) {
  const { showAlert } = useAlerts()
  const handleMutationError = useMutationErrorHandler()
  const { stableProjectId, ProjectSelector } = useProjectSelector({ requireProject: true })
  const [connectionToDisable, setConnectionToDisable] = useState<PluginConnection | null>(null)
  const [connectionToDelete, setConnectionToDelete] = useState<PluginConnection | null>(null)
  const [filters, setFilters] = useState<FilterConfig[]>([])
  const connectionsQuery = pluginPlatformClient.useQuery(
    'get',
    '/plugin_connections',
    {
      params: {
        query: {
          project_id: stableProjectId ?? '00000000-0000-0000-0000-000000000000',
          include_total: true,
        },
      },
    },
    { enabled: Boolean(stableProjectId) }
  )
  const { mutate: updateConnection, isPending: isUpdating } = pluginPlatformClient.useMutation(
    'patch',
    '/plugin_connections/{connection_id}'
  )
  const { mutate: deleteConnection, isPending: isDeleting } = pluginPlatformClient.useMutation(
    'delete',
    '/plugin_connections/{connection_id}'
  )
  const connections = connectionsQuery.data?.resources ?? []
  const isMutationPending = isUpdating || isDeleting
  const filteredConnections = (() => {
    const nameFilter = filters.find((filter) => filter.key === 'name')
    const term = String(nameFilter?.value ?? '')
      .trim()
      .toLowerCase()
    return term ? connections.filter((connection) => connection.name.toLowerCase().includes(term)) : connections
  })()
  const hasActiveFilters = filters.length > 0

  function refetchConnections() {
    detachPromise(connectionsQuery.refetch())
  }

  function updateEnabled(connection: PluginConnection, enabled: boolean) {
    updateConnection(
      {
        params: { path: { connection_id: connection.id } },
        body: { enabled },
      },
      {
        onSuccess: () => {
          showAlert({
            title: enabled ? 'Plugin integration enabled' : 'Plugin integration disabled',
            description: `"${connection.name}" is now ${enabled ? 'available' : 'unavailable'} to new workflow revisions.`,
            variant: 'success',
            autoDismiss: true,
          })
          refetchConnections()
        },
        onError: handleMutationError({ title: 'Failed to update plugin integration' }),
        onSettled: () => setConnectionToDisable(null),
      }
    )
  }

  function handleToggleEnabled(connection: PluginConnection) {
    if (connection.enabled) {
      setConnectionToDisable(connection)
      return
    }
    updateEnabled(connection, true)
  }

  function handleDelete() {
    if (!connectionToDelete) return
    deleteConnection(
      { params: { path: { connection_id: connectionToDelete.id } } },
      {
        onSuccess: () => {
          showAlert({
            title: 'Plugin integration deleted',
            description: `"${connectionToDelete.name}" and its encrypted credential have been deleted.`,
            variant: 'success',
            autoDismiss: true,
          })
          refetchConnections()
        },
        onError: handleMutationError({ title: 'Failed to delete plugin integration' }),
        onSettled: () => setConnectionToDelete(null),
      }
    )
  }

  return (
    <>
      <SynListPanel isFullHeight={false}>
        <StackItem>
          <Title headingLevel="h2" size="lg">
            Plugin integrations
          </Title>
          <Content component="p">
            Reusable project connections supplied by installed plugins. Their credentials remain private; workflows
            select a saved integration, not a token.
          </Content>
        </StackItem>
        <StackItem>
          <FormGroup label="Project" fieldId="plugin-integrations-project">
            {ProjectSelector}
          </FormGroup>
        </StackItem>
        <SynListPanelView
          isPending={connectionsQuery.isPending}
          isFetching={connectionsQuery.isFetching}
          error={connectionsQuery.error}
          onRetry={refetchConnections}
          isEmpty={!stableProjectId || filteredConnections.length === 0}
          hasActiveFilters={hasActiveFilters}
          onClearAllFilters={() => setFilters([])}
          noDataState={
            <Content component="p">
              {stableProjectId
                ? 'No plugin integrations are configured for this project yet.'
                : 'Select a project to view its plugin integrations.'}
            </Content>
          }
          toolbar={
            connections.length > 0 || hasActiveFilters ? (
              <SynListPanelToolbar
                filters={filters}
                filterDefinitions={[getIntegrationNameFilterDefinition()]}
                onFilterChange={setFilters}
                clearAllFilters={() => setFilters([])}
              />
            ) : undefined
          }
          body={
            <SynListPanelTable caption="Plugin integrations" useFixedLayout={false}>
              <PluginIntegrationTable
                connections={filteredConnections}
                contracts={contracts}
                onToggleEnabled={handleToggleEnabled}
                onDelete={setConnectionToDelete}
                isPending={isMutationPending}
              />
            </SynListPanelTable>
          }
        />
      </SynListPanel>
      <SynConfirmationDialog
        isOpen={Boolean(connectionToDisable)}
        onClose={() => setConnectionToDisable(null)}
        onConfirm={() => connectionToDisable && updateEnabled(connectionToDisable, false)}
        title="Disable plugin integration?"
        confirmLabel="Disable integration"
        confirmLoading={isUpdating}
      >
        <Content component="p">
          <strong>{connectionToDisable?.name}</strong> will remain available to existing workflow revisions, but cannot
          be selected by new or updated workflow revisions.
        </Content>
      </SynConfirmationDialog>
      <SynConfirmationDialog
        isOpen={Boolean(connectionToDelete)}
        onClose={() => setConnectionToDelete(null)}
        onConfirm={handleDelete}
        title="Delete plugin integration?"
        confirmLabel="Delete integration"
        confirmVariant="danger"
        titleIconVariant="warning"
        confirmLoading={isDeleting}
        destructiveAcknowledgement={{
          checkboxId: 'delete-plugin-integration-ack',
          label: 'I understand this deletes the encrypted credential and cannot be undone.',
        }}
      >
        <Content component="p">
          <strong>{connectionToDelete?.name}</strong> will be permanently deleted. Connections pinned by a workflow
          revision cannot be deleted.
        </Content>
      </SynConfirmationDialog>
    </>
  )
}
