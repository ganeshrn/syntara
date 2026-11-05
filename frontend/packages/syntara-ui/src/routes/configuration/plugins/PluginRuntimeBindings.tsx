import {
  Badge,
  Button,
  Content,
  DescriptionList,
  DescriptionListDescription,
  DescriptionListGroup,
  DescriptionListTerm,
  List,
  ListItem,
  Stack,
  StackItem,
  Title,
} from '@patternfly/react-core'
import type { PluginPlatformAPI } from '@syntara/contracts'
import type { ReactNode } from 'react'

import { pluginPlatformClient } from '../../../client'
import { SynPanel } from '../../../components/layout/SynPanel'
import { detachPromise } from '../../../utils/detachPromise'

import type { CatalogPlugin } from './pluginCatalog'

export type CatalogRuntimeBindingPreview =
  PluginPlatformAPI.components['schemas']['CatalogPluginRuntimeBindingPreviewRead']

function RuntimeBindingDetails({ preview }: Readonly<{ preview: CatalogRuntimeBindingPreview | undefined }>) {
  if (!preview || preview.status === 'not-required') {
    return <Content component="p">This release does not require publisher workload image bindings.</Content>
  }
  if (preview.status === 'missing') {
    return (
      <Content component="p">
        This plugin declares {preview.workload_ids.length} workload
        {preview.workload_ids.length === 1 ? '' : 's'}, but no RuntimeBinding is published for this release yet.
      </Content>
    )
  }
  if (preview.status === 'incomplete') {
    return (
      <>
        <Content component="p">Some declared workloads do not have a published RuntimeBinding.</Content>
        <List>
          {preview.missing_workload_ids.map((workloadId) => (
            <ListItem key={workloadId}>
              Missing binding for workload <code>{workloadId}</code>
            </ListItem>
          ))}
        </List>
        <RuntimeBindingList bindings={preview.runtime_bindings} />
      </>
    )
  }
  return <RuntimeBindingList bindings={preview.runtime_bindings} />
}

function RuntimeBindingList({ bindings }: Readonly<{ bindings: CatalogRuntimeBindingPreview['runtime_bindings'] }>) {
  if (bindings.length === 0)
    return <Content component="p">No workload bindings are available for this release.</Content>

  return (
    <Stack hasGutter>
      {bindings.map((binding) => (
        <StackItem key={`${binding.workload_id}:${binding.binding_digest}`}>
          <Title headingLevel="h3" size="lg">
            {binding.workload_id}
          </Title>
          <DescriptionList isHorizontal>
            <DescriptionListGroup>
              <DescriptionListTerm>Binding generation</DescriptionListTerm>
              <DescriptionListDescription>{binding.binding_index_generation}</DescriptionListDescription>
            </DescriptionListGroup>
            <DescriptionListGroup>
              <DescriptionListTerm>Binding digest</DescriptionListTerm>
              <DescriptionListDescription>
                <code>{binding.binding_digest}</code>
              </DescriptionListDescription>
            </DescriptionListGroup>
            <DescriptionListGroup>
              <DescriptionListTerm>Binding index digest</DescriptionListTerm>
              <DescriptionListDescription>
                <code>{binding.binding_index_digest}</code>
              </DescriptionListDescription>
            </DescriptionListGroup>
          </DescriptionList>
          <List>
            {binding.options.map((option) => (
              <ListItem key={option.name}>
                <strong>{option.display_name}</strong> <code>({option.name})</code>{' '}
                {option.name === binding.default_option ? <Badge isRead>Default</Badge> : null}
                {option.description ? <Content component="p">{option.description}</Content> : null}
                <Content component="p">
                  Image digest: <code>{option.image_digest}</code>
                </Content>
              </ListItem>
            ))}
          </List>
        </StackItem>
      ))}
    </Stack>
  )
}

function queryErrorMessage(error: unknown): string | undefined {
  if (typeof error === 'string') return error
  if (error instanceof Error) return error.message
  return error ? 'The catalog service could not load the binding preview.' : undefined
}

export function PluginRuntimeBindings({ plugin }: Readonly<{ plugin: CatalogPlugin }>) {
  const hasPublisherWorkload = plugin.runtime_ownerships.includes('custom-workload')
  const query = pluginPlatformClient.useQuery(
    'get',
    '/catalog/plugins/{namespace}/{name}/versions/{version}/runtime-bindings',
    {
      params: {
        path: { namespace: plugin.namespace, name: plugin.name, version: plugin.version },
        query: { source_id: plugin.source_id },
      },
    },
    { enabled: hasPublisherWorkload }
  )

  let content: ReactNode
  if (!hasPublisherWorkload) {
    content = (
      <Content component="p">This release uses platform-managed runners and does not publish workload images.</Content>
    )
  } else if (query.isPending) {
    content = <Content component="p">Checking the RuntimeBinding and its image digests…</Content>
  } else if (query.error) {
    content = (
      <Content component="p">
        Could not load the RuntimeBinding preview: {queryErrorMessage(query.error)}{' '}
        <Button variant="link" isInline onClick={() => detachPromise(query.refetch())}>
          Retry
        </Button>
      </Content>
    )
  } else {
    content = <RuntimeBindingDetails preview={query.data} />
  }

  return (
    <SynPanel>
      <Title headingLevel="h2">Runtime bindings</Title>
      <Content component="p">
        Runtime bindings are published separately and tied to this plugin artifact digest, so workload images can be
        updated without changing the plugin release. Syntara verifies the binding and each image digest before showing
        the options used by the workflow editor.
      </Content>
      {content}
    </SynPanel>
  )
}
