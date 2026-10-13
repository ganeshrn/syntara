import {
  Badge,
  Button,
  Card,
  CardBody,
  CardTitle,
  Content,
  DescriptionList,
  DescriptionListDescription,
  DescriptionListGroup,
  DescriptionListTerm,
  Flex,
  FlexItem,
  List,
  ListItem,
  Stack,
  StackItem,
  Title,
} from '@patternfly/react-core'
import type { PluginPlatformAPI } from '@syntara/contracts'
import { useNavigate, useParams } from '@tanstack/react-router'
import { useState } from 'react'

import { AppRoute } from '../../../app/AppRoute'
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

import { comparePluginVersions, displayPluginName, type CatalogPlugin, type InstalledPlugin } from './pluginCatalog'
import { PluginDocumentation } from './PluginDocumentation'

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

function PluginNotFound() {
  const navigate = useNavigate()
  return (
    <SynPage>
      <SynPageTitle segments={['Plugin not found', 'Plugins']} />
      <SynPageHeader title="Plugin not found" />
      <SynPageBody>
        <SynPanel>
          <Content component="p">This plugin is no longer available from the selected catalog source.</Content>
          <Button
            variant="primary"
            onClick={() => detachPromise(navigate({ to: AppRoute.Configuration.Plugins.Root }))}
          >
            Return to plugins
          </Button>
        </SynPanel>
      </SynPageBody>
    </SynPage>
  )
}

function PluginOverview({
  plugin,
  sourceId,
  releaseCount,
}: Readonly<{ plugin: CatalogPlugin; sourceId: string; releaseCount: number }>) {
  return (
    <SynPanel>
      <Title headingLevel="h2">Overview</Title>
      <Content component="p">
        This entity groups the immutable releases of{' '}
        <strong>
          {plugin.namespace}/{plugin.name}
        </strong>{' '}
        from the
        <strong> {sourceId}</strong> catalog source. Select a release below to inspect its trusted metadata.
      </Content>
      <DescriptionList isHorizontal>
        <DescriptionListGroup>
          <DescriptionListTerm>Selected release</DescriptionListTerm>
          <DescriptionListDescription>{plugin.version}</DescriptionListDescription>
        </DescriptionListGroup>
        <DescriptionListGroup>
          <DescriptionListTerm>Available releases</DescriptionListTerm>
          <DescriptionListDescription>{releaseCount}</DescriptionListDescription>
        </DescriptionListGroup>
        <DescriptionListGroup>
          <DescriptionListTerm>Source</DescriptionListTerm>
          <DescriptionListDescription>{plugin.source_id}</DescriptionListDescription>
        </DescriptionListGroup>
      </DescriptionList>
    </SynPanel>
  )
}

function PluginReleases({
  versions,
  selectedVersion,
  installedPlugins,
  onSelect,
}: Readonly<{
  versions: readonly CatalogPlugin[]
  selectedVersion: string
  installedPlugins: readonly InstalledPlugin[]
  onSelect: (version: string) => void
}>) {
  return (
    <SynPanel>
      <Title headingLevel="h2">Releases</Title>
      <Content component="p">Each release stays pinned to its own verified artifact digest.</Content>
      <List isPlain>
        {versions.map((plugin) => {
          const isSelected = plugin.version === selectedVersion
          const isInstalled = installedPlugins.some(
            (installed) =>
              installed.namespace === plugin.namespace &&
              installed.name === plugin.name &&
              installed.version === plugin.version
          )
          return (
            <ListItem key={plugin.id}>
              <Card isCompact isSelectable isSelected={isSelected}>
                <CardTitle>
                  <Flex alignItems={{ default: 'alignItemsCenter' }} gap={{ default: 'gapSm' }}>
                    <FlexItem>{plugin.version}</FlexItem>
                    {isInstalled ? (
                      <FlexItem>
                        <Badge isRead>Installed</Badge>
                      </FlexItem>
                    ) : null}
                    {plugin.deprecated ? (
                      <FlexItem>
                        <Badge isRead>Deprecated</Badge>
                      </FlexItem>
                    ) : null}
                  </Flex>
                </CardTitle>
                <CardBody>
                  <Button variant="link" isInline onClick={() => onSelect(plugin.version)}>
                    {isSelected ? 'Selected release' : 'Inspect release'}
                  </Button>
                </CardBody>
              </Card>
            </ListItem>
          )
        })}
      </List>
    </SynPanel>
  )
}

type InstalledPluginAction = PluginPlatformAPI.components['schemas']['InstalledPluginActionRead']

type InputField = Readonly<{
  name: string
  type: string
  description: string | undefined
  required: boolean
}>

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined
}

function inputFields(schema: Record<string, unknown>): readonly InputField[] {
  const properties = asRecord(schema.properties)
  if (!properties) return []
  const required = new Set(
    Array.isArray(schema.required) ? schema.required.filter((field): field is string => typeof field === 'string') : []
  )
  return Object.entries(properties).map(([name, definition]) => {
    const field = asRecord(definition)
    return {
      name,
      type: typeof field?.type === 'string' ? field.type : 'value',
      description: typeof field?.description === 'string' ? field.description : undefined,
      required: required.has(name),
    }
  })
}

function ActionInputContract({ action }: Readonly<{ action: InstalledPluginAction }>) {
  const fields = inputFields(action.input_schema ?? {})
  if (fields.length === 0) return <Content component="p">This step does not declare named workflow inputs.</Content>

  return (
    <>
      <Title headingLevel="h3" size="lg">
        Input contract
      </Title>
      <Content component="p">These fields come from this installed action revision’s manifest.</Content>
      <List>
        {fields.map((field) => (
          <ListItem key={field.name}>
            <strong>{field.name}</strong> <code>({field.type})</code>{' '}
            <Badge isRead>{field.required ? 'Required' : 'Optional'}</Badge>
            {field.description ? ` — ${field.description}` : null}
          </ListItem>
        ))}
      </List>
    </>
  )
}

function WorkflowSteps({ actions }: Readonly<{ actions: readonly InstalledPluginAction[] | undefined }>) {
  return (
    <SynPanel>
      <Title headingLevel="h2">Workflow steps</Title>
      {actions ? (
        <List>
          {actions.map((action) => (
            <ListItem key={action.step_revision_id}>
              <strong>{action.display_name}</strong> — {action.description}
              <ActionInputContract action={action} />
            </ListItem>
          ))}
        </List>
      ) : (
        <Content component="p">
          Approve and install this release to record its workflow-step contracts in Syntara.
        </Content>
      )}
    </SynPanel>
  )
}

function PluginIntegrationSetup({
  integrationTypes,
}: Readonly<{
  integrationTypes: readonly PluginPlatformAPI.components['schemas']['InstalledPluginIntegrationTypeRead'][] | undefined
}>) {
  const navigate = useNavigate()
  if (!integrationTypes?.length) return null
  return (
    <SynPanel>
      <Title headingLevel="h2">Integration setup</Title>
      <Content component="p">
        This plugin contributes reusable integration types. Configure a named project integration before adding a
        related workflow step.
      </Content>
      <List>
        {integrationTypes.map((integrationType) => (
          <ListItem key={integrationType.id}>
            <strong>{integrationType.display_name}</strong> — {integrationType.description}
          </ListItem>
        ))}
      </List>
      <Button
        variant="secondary"
        onClick={() => detachPromise(navigate({ to: AppRoute.Configuration.PluginIntegrations.Root }))}
      >
        Configure plugin integrations
      </Button>
    </SynPanel>
  )
}

function selectedPluginVersions(
  plugins: readonly CatalogPlugin[] | undefined,
  sourceId: string,
  namespace: string,
  pluginName: string
): readonly CatalogPlugin[] {
  return (plugins ?? [])
    .filter((plugin) => plugin.source_id === sourceId && plugin.namespace === namespace && plugin.name === pluginName)
    .sort((a, b) => comparePluginVersions(b.version, a.version))
}

function installedPluginFor(
  plugins: readonly InstalledPlugin[] | undefined,
  selectedPlugin: CatalogPlugin | undefined
): InstalledPlugin | undefined {
  if (!selectedPlugin) return undefined
  return (plugins ?? []).find(
    (plugin) =>
      plugin.namespace === selectedPlugin.namespace &&
      plugin.name === selectedPlugin.name &&
      plugin.version === selectedPlugin.version
  )
}

function installedDetailPath(selectedPlugin: CatalogPlugin | undefined, namespace: string, pluginName: string) {
  return {
    namespace: selectedPlugin?.namespace ?? namespace,
    name: selectedPlugin?.name ?? pluginName,
    version: selectedPlugin?.version ?? '0.0.0',
  }
}

function PluginVerification({ plugin }: Readonly<{ plugin: CatalogPlugin }>) {
  return (
    <SynPanel>
      <Title headingLevel="h2">Verification</Title>
      <DescriptionList isHorizontal>
        <DescriptionListGroup>
          <DescriptionListTerm>Artifact repository</DescriptionListTerm>
          <DescriptionListDescription>{plugin.artifact_repository}</DescriptionListDescription>
        </DescriptionListGroup>
        <DescriptionListGroup>
          <DescriptionListTerm>Verified artifact digest</DescriptionListTerm>
          <DescriptionListDescription>{plugin.artifact_digest}</DescriptionListDescription>
        </DescriptionListGroup>
        <DescriptionListGroup>
          <DescriptionListTerm>Documentation digest</DescriptionListTerm>
          <DescriptionListDescription>{plugin.documentation_digest}</DescriptionListDescription>
        </DescriptionListGroup>
      </DescriptionList>
    </SynPanel>
  )
}

function PluginDetailPage({
  sourceId,
  pluginName,
  selectedPlugin,
  versions,
  installedPlugin,
  installedPlugins,
  workflowActions,
  integrationTypes,
  canInstall,
  isChecking,
  isInstalling,
  onInstall,
  onSelectVersion,
  pluginDocLink,
}: Readonly<{
  sourceId: string
  pluginName: string
  selectedPlugin: CatalogPlugin
  versions: readonly CatalogPlugin[]
  installedPlugin: InstalledPlugin | undefined
  installedPlugins: readonly InstalledPlugin[]
  workflowActions: readonly InstalledPluginAction[] | undefined
  integrationTypes?: readonly PluginPlatformAPI.components['schemas']['InstalledPluginIntegrationTypeRead'][]
  canInstall: boolean
  isChecking: boolean
  isInstalling: boolean
  onInstall: () => void
  onSelectVersion: (version: string) => void
  pluginDocLink: string
}>) {
  const displayName = displayPluginName(pluginName)
  const breadcrumbs = [{ label: 'Plugins', href: AppRoute.Configuration.Plugins.Root }, { label: displayName }]

  return (
    <SynPage>
      <SynPageTitle segments={[displayName, 'Plugins']} />
      <SynPageHeader
        breadcrumbs={breadcrumbs}
        title={displayName}
        docLink={pluginDocLink}
        titleAddons={
          <FlexItem>
            <Flex gap={{ default: 'gapSm' }}>
              <FlexItem>
                <RuntimeOwnershipBadges ownerships={selectedPlugin.runtime_ownerships} />
              </FlexItem>
              {installedPlugin ? (
                <FlexItem>
                  <Badge isRead>Installed {installedPlugin.version}</Badge>
                </FlexItem>
              ) : null}
            </Flex>
          </FlexItem>
        }
        toolbar={
          <DisabledWithTooltip
            isDisabled={!canInstall || isChecking}
            content={permissionTooltip('approve and install this plugin', 'plugin_registry:install')}
          >
            <Button
              variant="primary"
              isAriaDisabled={!canInstall || isChecking}
              isLoading={isInstalling}
              onClick={canInstall ? onInstall : undefined}
            >
              {installedPlugin ? 'Install again' : 'Approve and install'}
            </Button>
          </DisabledWithTooltip>
        }
      />
      <SynPageBody>
        <Stack hasGutter>
          <StackItem>
            <PluginOverview plugin={selectedPlugin} sourceId={sourceId} releaseCount={versions.length} />
          </StackItem>
          <StackItem>
            <PluginReleases
              versions={versions}
              selectedVersion={selectedPlugin.version}
              installedPlugins={installedPlugins}
              onSelect={onSelectVersion}
            />
          </StackItem>
          <StackItem>
            <WorkflowSteps actions={workflowActions} />
          </StackItem>
          <StackItem>
            <PluginIntegrationSetup integrationTypes={integrationTypes} />
          </StackItem>
          <StackItem>
            <SynPanel>
              <Title headingLevel="h2">Documentation</Title>
              <PluginDocumentation plugin={selectedPlugin} />
            </SynPanel>
          </StackItem>
          <StackItem>
            <PluginVerification plugin={selectedPlugin} />
          </StackItem>
        </Stack>
      </SynPageBody>
    </SynPage>
  )
}

/** Backstage-style entity inspection for one source-qualified plugin identity. */
export function PluginDetail() {
  const { sourceId, namespace, pluginName }: { sourceId: string; namespace: string; pluginName: string } = useParams({
    strict: false,
  })
  const pluginDocLink = useDocLink('plugins')
  const { allowed: canInstall, isChecking } = useCanI('install', 'plugin_registry')
  const { showSuccess } = useAlerts()
  const handleMutationError = useMutationErrorHandler()
  const [selectedVersion, setSelectedVersion] = useState<string | undefined>()

  const catalogQuery = pluginPlatformClient.useQuery('get', '/catalog/plugins', {
    params: { query: { source_id: sourceId, namespace, name: pluginName, include_total: true } },
  })
  const installedQuery = pluginPlatformClient.useQuery('get', '/plugin_registry', {
    params: { query: { include_total: true } },
  })
  const { mutate: installPlugin, isPending: isInstalling } = pluginPlatformClient.useMutation(
    'post',
    '/catalog/plugins/{namespace}/{name}/versions/{version}/install'
  )
  const versions = selectedPluginVersions(catalogQuery.data?.resources, sourceId, namespace, pluginName)
  const selectedPlugin = versions.find((plugin) => plugin.version === selectedVersion) ?? versions[0]
  const installedPlugin = installedPluginFor(installedQuery.data?.resources, selectedPlugin)
  const installedDetailQuery = pluginPlatformClient.useQuery(
    'get',
    '/plugin_registry/{namespace}/{name}/versions/{version}',
    {
      params: {
        path: installedDetailPath(selectedPlugin, namespace, pluginName),
      },
    },
    { enabled: Boolean(selectedPlugin && installedPlugin) }
  )

  if (catalogQuery.isPending || installedQuery.isPending) return <SynLoadingState />
  if (catalogQuery.error)
    return <SynErrorState message={catalogQuery.error} onRetry={() => detachPromise(catalogQuery.refetch())} />
  if (!selectedPlugin) return <PluginNotFound />

  const install = () => {
    installPlugin(
      {
        params: {
          path: { namespace: selectedPlugin.namespace, name: selectedPlugin.name, version: selectedPlugin.version },
          query: { source_id: selectedPlugin.source_id },
        },
      },
      {
        onSuccess: (result) => {
          showSuccess({
            title: result.idempotent ? 'Plugin is already installed' : 'Plugin approved and installed',
            description: `Active registry generation ${result.registry_generation}.`,
          })
          detachPromise(installedQuery.refetch())
          detachPromise(installedDetailQuery.refetch())
        },
        onError: handleMutationError({ title: 'Failed to approve and install plugin' }),
      }
    )
  }

  return (
    <PluginDetailPage
      sourceId={sourceId}
      pluginName={pluginName}
      selectedPlugin={selectedPlugin}
      versions={versions}
      installedPlugin={installedPlugin}
      installedPlugins={installedQuery.data?.resources ?? []}
      workflowActions={installedDetailQuery.data?.actions}
      integrationTypes={installedDetailQuery.data?.integration_types}
      canInstall={canInstall}
      isChecking={isChecking}
      isInstalling={isInstalling}
      onInstall={install}
      onSelectVersion={setSelectedVersion}
      pluginDocLink={pluginDocLink}
    />
  )
}
