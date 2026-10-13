import { Button, Content, Stack, StackItem } from '@patternfly/react-core'

import { pluginPlatformClient } from '../../../client'
import { DisabledWithTooltip } from '../../../components/DisabledWithTooltip'
import { SynLoadingState } from '../../../components/states/SynLoadingState'
import { permissionTooltip } from '../../../hooks/permissionUtils'
import { useCanI } from '../../../hooks/useCanI'
import { useMutationErrorHandler } from '../../../hooks/useMutationErrorHandler'
import { useAlerts } from '../../../providers/alerts'
import { detachPromise } from '../../../utils/detachPromise'

import type { CatalogPlugin } from './pluginCatalog'

/** Render only markup the control plane previously sanitized and persisted. */
export function PluginDocumentation({ plugin }: Readonly<{ plugin: CatalogPlugin }>) {
  const { allowed: canHydrate, isChecking } = useCanI('hydrate_documentation', 'plugin_catalog')
  const { showSuccess } = useAlerts()
  const handleMutationError = useMutationErrorHandler()
  const documentationQuery = pluginPlatformClient.useQuery(
    'get',
    '/catalog/plugins/{namespace}/{name}/versions/{version}/documentation',
    {
      params: {
        path: { namespace: plugin.namespace, name: plugin.name, version: plugin.version },
        query: { source_id: plugin.source_id },
      },
    }
  )
  const { mutate: hydrateDocumentation, isPending: isHydrating } = pluginPlatformClient.useMutation(
    'post',
    '/catalog/plugins/{namespace}/{name}/versions/{version}/documentation/hydrate'
  )

  if (documentationQuery.isPending) return <SynLoadingState />

  if (documentationQuery.error) {
    return (
      <Stack hasGutter>
        <StackItem>
          <Content component="p">Documentation has not been prepared for this plugin version.</Content>
        </StackItem>
        <StackItem>
          <DisabledWithTooltip
            isDisabled={!canHydrate || isChecking}
            content={permissionTooltip('prepare plugin documentation', 'plugin_catalog:hydrate_documentation')}
          >
            <Button
              variant="secondary"
              isAriaDisabled={!canHydrate || isChecking}
              isLoading={isHydrating}
              onClick={
                canHydrate
                  ? () =>
                      hydrateDocumentation(
                        {
                          params: {
                            path: { namespace: plugin.namespace, name: plugin.name, version: plugin.version },
                            query: { source_id: plugin.source_id },
                          },
                        },
                        {
                          onSuccess: () => {
                            showSuccess({
                              title: 'Documentation prepared',
                              description: 'The verified plugin documentation is ready.',
                            })
                            detachPromise(documentationQuery.refetch())
                          },
                          onError: handleMutationError({ title: 'Failed to prepare plugin documentation' }),
                        }
                      )
                  : undefined
              }
            >
              Prepare documentation
            </Button>
          </DisabledWithTooltip>
        </StackItem>
      </Stack>
    )
  }

  const documentation = documentationQuery.data
  if (!documentation) return null

  return <div dangerouslySetInnerHTML={{ __html: documentation.rendered_html }} />
}
