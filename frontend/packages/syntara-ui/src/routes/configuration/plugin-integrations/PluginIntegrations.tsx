import { AppRoute } from '../../../app/AppRoute'
import { SynPage, SynPageBody } from '../../../components/layout/SynPage'
import { SynPageHeader } from '../../../components/layout/SynPageHeader'
import { SynPanel } from '../../../components/layout/SynPanel'
import { SynErrorState } from '../../../components/states/SynErrorState'
import { SynLoadingState } from '../../../components/states/SynLoadingState'
import { SynPageTitle } from '../../../components/SynPageTitle'
import { detachPromise } from '../../../utils/detachPromise'

import { PluginIntegrationsPanel } from './PluginIntegrationsPanel'
import { useInstalledPluginIntegrationContracts } from './useInstalledPluginIntegrationContracts'

/**
 * Project-scoped setup for the integration types contributed by installed plugins.
 *
 * This route is reached from the unified Integrations configure menu. Plugin
 * instances remain separate from adapter-backed Integration records because they
 * preserve an immutable plugin type revision, but users discover and manage both
 * kinds from the same configuration surface.
 */
export function PluginIntegrations() {
  const { contracts, error, isPending, refetch } = useInstalledPluginIntegrationContracts()

  if (isPending) return <SynLoadingState />
  if (error) return <SynErrorState message={error} onRetry={() => detachPromise(refetch())} />

  const integrationTypes = contracts.map((contract) => contract.integrationType)
  const credentialTypes = contracts.flatMap((contract) => contract.credentialTypes)
  const sourcePluginByIntegrationTypeId = new Map(
    contracts.map((contract) => [contract.integrationType.id, contract.sourcePlugin.displayName])
  )

  return (
    <SynPage>
      <SynPageTitle segments={['Plugin integrations', 'Configuration']} />
      <SynPageHeader
        breadcrumbs={[
          { label: 'Configuration', href: AppRoute.Configuration.Integrations.Root },
          { label: 'Plugin integrations' },
        ]}
        title="Plugin integrations"
      />
      <SynPageBody>
        {integrationTypes.length > 0 ? (
          <PluginIntegrationsPanel
            integrationTypes={integrationTypes}
            credentialTypes={credentialTypes}
            sourcePluginByIntegrationTypeId={sourcePluginByIntegrationTypeId}
            initialIntegrationTypeId={
              new URLSearchParams(window.location.search).get('integration_type_revision_id') ?? undefined
            }
          />
        ) : (
          <SynPanel>
            No installed plugin contributes an integration type yet. Approve and install a plugin such as GitHub first.
          </SynPanel>
        )}
      </SynPageBody>
    </SynPage>
  )
}
