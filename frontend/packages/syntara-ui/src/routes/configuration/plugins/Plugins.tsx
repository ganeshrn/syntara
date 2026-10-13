import {
  Badge,
  Button,
  Card,
  CardBody,
  CardTitle,
  Content,
  EmptyState,
  EmptyStateBody,
  Flex,
  FlexItem,
  Gallery,
  GalleryItem,
  Spinner,
  Stack,
  StackItem,
  Title,
} from '@patternfly/react-core'
import { RhUiAddIcon, RhUiRefreshIcon } from '@patternfly/react-icons'
import type { PluginPlatformAPI } from '@syntara/contracts'
import { useNavigate } from '@tanstack/react-router'
import { useState } from 'react'

import { pluginPlatformClient } from '../../../client'
import { DisabledWithTooltip } from '../../../components/DisabledWithTooltip'
import { SynPage, SynPageBody } from '../../../components/layout/SynPage'
import { SynPageHeader } from '../../../components/layout/SynPageHeader'
import { SynPanel } from '../../../components/layout/SynPanel'
import { SynErrorState } from '../../../components/states/SynErrorState'
import { SynLoadingState } from '../../../components/states/SynLoadingState'
import { SynPageTitle } from '../../../components/SynPageTitle'
import { permissionTooltip } from '../../../hooks/permissionUtils'
import { useCanI } from '../../../hooks/useCanI'
import { useMutationErrorHandler } from '../../../hooks/useMutationErrorHandler'
import { useAlerts } from '../../../providers/alerts'
import { detachPromise } from '../../../utils/detachPromise'
import { useDocLink } from '../../../utils/docs/useDocLink'

import {
  displayPluginName,
  groupCatalogPlugins,
  type CatalogPlugin,
  type PluginCard,
  type PluginSource,
} from './pluginCatalog'
import { PluginSourceDialog } from './PluginSourceDialog'

function RuntimeOwnershipBadges({ ownerships }: Readonly<{ ownerships: CatalogPlugin['runtime_ownerships'] }>) {
  return (
    <>
      {ownerships.map((ownership) => (
        <Badge isRead key={ownership}>
          {ownership === 'platform-runner' ? 'Syntara managed' : 'Publisher workload'}
        </Badge>
      ))}
    </>
  )
}

function CatalogSources({
  sources,
  isLoading,
  onRefresh,
}: Readonly<{
  sources: PluginSource[]
  isLoading: boolean
  onRefresh: (source: PluginSource) => void
}>) {
  const { allowed: canReconcile, isChecking } = useCanI('reconcile', 'plugin_source')

  if (isLoading) return <Spinner size="md" aria-label="Loading plugin catalogs" />

  if (sources.length === 0) {
    return <Content component="p">No local plugin catalog is configured.</Content>
  }

  return (
    <Stack hasGutter>
      {sources.map((source) => (
        <StackItem key={source.id}>
          <Flex justifyContent={{ default: 'justifyContentSpaceBetween' }} alignItems={{ default: 'alignItemsCenter' }}>
            <FlexItem>
              <Stack>
                <StackItem>
                  <strong>{source.display_name}</strong>
                </StackItem>
                <StackItem>
                  <Content component="small">
                    {source.registry_origin}/{source.index_repository}:{source.channel}
                  </Content>
                </StackItem>
              </Stack>
            </FlexItem>
            <FlexItem>
              <DisabledWithTooltip
                isDisabled={!canReconcile || isChecking}
                content={permissionTooltip('refresh this catalog', 'plugin_source:reconcile')}
              >
                <Button
                  variant="secondary"
                  icon={<RhUiRefreshIcon />}
                  isAriaDisabled={!canReconcile || isChecking}
                  onClick={canReconcile ? () => onRefresh(source) : undefined}
                >
                  Refresh catalog
                </Button>
              </DisabledWithTooltip>
            </FlexItem>
          </Flex>
        </StackItem>
      ))}
    </Stack>
  )
}

function MarketplaceBody({
  pluginCards,
  isLoading,
  error,
  onRetry,
  onInspect,
}: Readonly<{
  pluginCards: readonly PluginCard[]
  isLoading: boolean
  error: unknown
  onRetry: () => void
  onInspect: (plugin: PluginCard) => void
}>) {
  if (error) return <SynErrorState message={error} onRetry={onRetry} />
  if (isLoading) return <SynLoadingState />
  if (pluginCards.length === 0) {
    return (
      <EmptyState headingLevel="h2" titleText="No plugins discovered yet">
        <EmptyStateBody>Add a catalog source and refresh it to discover plugin artifacts.</EmptyStateBody>
      </EmptyState>
    )
  }
  return (
    <Gallery hasGutter minWidths={{ default: '280px' }}>
      {pluginCards.map((plugin) => (
        <GalleryItem key={`${plugin.sourceId}:${plugin.namespace}/${plugin.name}`}>
          <Card isCompact>
            <CardTitle>
              <Stack hasGutter>
                <StackItem>
                  <Title headingLevel="h3" size="lg">
                    {displayPluginName(plugin.name)}
                  </Title>
                  <Content component="small">
                    {plugin.namespace}/{plugin.name}
                  </Content>
                </StackItem>
                <StackItem>
                  <Flex flexWrap={{ default: 'wrap' }} gap={{ default: 'gapSm' }}>
                    <FlexItem>
                      <Badge isRead>{plugin.selectedVersion.version}</Badge>
                    </FlexItem>
                    <FlexItem>
                      <RuntimeOwnershipBadges ownerships={plugin.selectedVersion.runtime_ownerships} />
                    </FlexItem>
                    <FlexItem>
                      <Badge isRead>
                        {plugin.versions.length} release{plugin.versions.length === 1 ? '' : 's'}
                      </Badge>
                    </FlexItem>
                    {plugin.installedVersion ? (
                      <FlexItem>
                        <Badge isRead>Installed {plugin.installedVersion.version}</Badge>
                      </FlexItem>
                    ) : null}
                  </Flex>
                </StackItem>
              </Stack>
            </CardTitle>
            <CardBody>
              <Stack hasGutter>
                <StackItem>
                  <Content component="p">
                    A trusted plugin from the <strong>{plugin.sourceId}</strong> catalog.
                  </Content>
                </StackItem>
                <StackItem>
                  <Button variant="link" isInline onClick={() => onInspect(plugin)}>
                    Inspect plugin
                  </Button>
                </StackItem>
              </Stack>
            </CardBody>
          </Card>
        </GalleryItem>
      ))}
    </Gallery>
  )
}

export function Plugins() {
  const navigate = useNavigate()
  const pluginDocLink = useDocLink('plugins')
  const { allowed: canCreateSource, isChecking: isCheckingSourceCreate } = useCanI('create', 'plugin_source')
  const { showSuccess } = useAlerts()
  const handleMutationError = useMutationErrorHandler()
  const [isSourceDialogOpen, setIsSourceDialogOpen] = useState(false)

  const sourceQuery = pluginPlatformClient.useQuery(
    'get',
    '/plugin_sources',
    { params: { query: { include_total: true } } },
    {
      enabled: canCreateSource,
    }
  )
  const catalogQuery = pluginPlatformClient.useQuery('get', '/catalog/plugins', {
    params: { query: { include_total: true } },
  })
  const installedQuery = pluginPlatformClient.useQuery('get', '/plugin_registry', {
    params: { query: { include_total: true } },
  })
  const { mutate: createSource, isPending: isCreatingSource } = pluginPlatformClient.useMutation(
    'post',
    '/plugin_sources'
  )
  const { mutate: reconcileSource, isPending: isReconciling } = pluginPlatformClient.useMutation(
    'post',
    '/plugin_sources/{plugin_source_id}/reconcile'
  )
  const refreshCatalog = (source: PluginSource) => {
    reconcileSource(
      { params: { path: { plugin_source_id: source.id } } },
      {
        onSuccess: (result) => {
          showSuccess({
            title: result.idempotent ? 'Catalog is already current' : 'Catalog refreshed',
            description: `${result.entry_count} plugin version${result.entry_count === 1 ? '' : 's'} discovered.`,
          })
          detachPromise(catalogQuery.refetch())
          detachPromise(sourceQuery.refetch())
        },
        onError: handleMutationError({ title: 'Failed to refresh catalog' }),
      }
    )
  }
  const addSource = (source: PluginPlatformAPI.components['schemas']['PluginSourceCreate']) => {
    createSource(
      { body: source },
      {
        onSuccess: (createdSource) => {
          showSuccess({ title: 'Catalog added', description: `${createdSource.display_name} is ready to refresh.` })
          setIsSourceDialogOpen(false)
          detachPromise(sourceQuery.refetch())
        },
        onError: handleMutationError({ title: 'Failed to add catalog' }),
      }
    )
  }
  const catalogPlugins = catalogQuery.data?.resources ?? []
  const pluginCards = groupCatalogPlugins(catalogPlugins, installedQuery.data?.resources ?? [])
  return (
    <SynPage>
      <SynPageTitle segments={['Plugins']} />
      <SynPageHeader
        title="Plugins"
        docLink={pluginDocLink}
        toolbar={
          <DisabledWithTooltip
            isDisabled={!canCreateSource || isCheckingSourceCreate}
            content={permissionTooltip('add a plugin catalog', 'plugin_source:create')}
          >
            <Button
              variant="primary"
              icon={<RhUiAddIcon />}
              isAriaDisabled={!canCreateSource || isCheckingSourceCreate}
              onClick={canCreateSource ? () => setIsSourceDialogOpen(true) : undefined}
            >
              Add local catalog
            </Button>
          </DisabledWithTooltip>
        }
      />
      <SynPageBody>
        <Stack hasGutter>
          {canCreateSource ? (
            <StackItem>
              <SynPanel>
                <Title headingLevel="h2">Catalog sources</Title>
                <Content component="p">
                  A catalog source points Syntara to a registry-hosted index. Refreshing it only reads approved plugin
                  metadata into the catalog; it does not install or execute a plugin.
                </Content>
                <CatalogSources
                  sources={sourceQuery.data?.resources ?? []}
                  isLoading={sourceQuery.isPending || isReconciling}
                  onRefresh={refreshCatalog}
                />
              </SynPanel>
            </StackItem>
          ) : null}
          <StackItem>
            <SynPanel>
              <Title headingLevel="h2">Marketplace</Title>
              <Content component="p">
                Browse verified plugin versions from configured catalogs. For this local proof of concept, an
                administrator's approval and installation are one action: it creates an immutable active registry
                revision. Builder placement is the next workflow-authoring increment.
              </Content>
              <MarketplaceBody
                pluginCards={pluginCards}
                isLoading={catalogQuery.isPending || installedQuery.isPending}
                error={catalogQuery.error}
                onRetry={() => detachPromise(catalogQuery.refetch())}
                onInspect={(plugin) =>
                  detachPromise(
                    navigate({
                      to: '/configuration/plugins/$sourceId/$namespace/$pluginName',
                      params: { sourceId: plugin.sourceId, namespace: plugin.namespace, pluginName: plugin.name },
                    })
                  )
                }
              />
            </SynPanel>
          </StackItem>
        </Stack>
      </SynPageBody>
      <PluginSourceDialog
        isOpen={isSourceDialogOpen}
        isCreating={isCreatingSource}
        onClose={() => setIsSourceDialogOpen(false)}
        onCreate={addSource}
      />
    </SynPage>
  )
}
