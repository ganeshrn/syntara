import { AppRoute } from '../../../app/AppRoute'

/** Build the deep link that opens a schema-driven plugin integration form. */
export function pluginIntegrationSetupPath(integrationTypeRevisionId: string): string {
  return `${AppRoute.Configuration.PluginIntegrations.Root}?integration_type_revision_id=${encodeURIComponent(integrationTypeRevisionId)}`
}
