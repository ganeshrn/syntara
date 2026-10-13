import { describe, expect, it } from 'vitest'

import { comparePluginVersions, displayPluginName, groupCatalogPlugins, type CatalogPlugin } from './pluginCatalog'

function plugin(version: string, sourceId = 'local-quay'): CatalogPlugin {
  return {
    id: `${sourceId}-${version}`,
    source_id: sourceId,
    reconciliation_id: 'reconciliation-1',
    namespace: 'syntara',
    name: 'github',
    version,
    artifact_repository: 'syntara-admin/plugins/github',
    artifact_digest: `sha256:${version}`,
    documentation_digest: `sha256:documentation-${version}`,
    runtime_ownerships: ['platform-runner'],
    deprecated: false,
    withdrawn: false,
    created_at: '2026-10-08T03:30:36Z',
    updated_at: '2026-10-08T03:30:36Z',
  }
}

describe('plugin catalog presentation', () => {
  it('sorts semantic versions, with a release after its prerelease', () => {
    expect(comparePluginVersions('0.1.10', '0.1.4')).toBeGreaterThan(0)
    expect(comparePluginVersions('1.0.0', '1.0.0-rc.1')).toBeGreaterThan(0)
  })

  it('groups releases into one plugin card and selects the newest release', () => {
    const cards = groupCatalogPlugins([plugin('0.1.4'), plugin('0.1.10')], [])

    expect(cards).toHaveLength(1)
    expect(cards[0]).toMatchObject({ name: 'github', selectedVersion: { version: '0.1.10' } })
    expect(cards[0]?.versions.map((version) => version.version)).toEqual(['0.1.10', '0.1.4'])
  })

  it('only shows an installed release when it belongs to the catalog card', () => {
    const installedPlugin = {
      id: 'installed-1',
      namespace: 'syntara',
      name: 'github',
      version: '9.9.9',
      display_name: 'GitHub',
      description: 'GitHub actions',
      artifact_repository: 'syntara-admin/plugins/github',
      artifact_digest: 'sha256:installed',
      action_count: 1,
      enabled: true,
      installed_at: '2026-10-08T03:30:36Z',
    }

    expect(groupCatalogPlugins([plugin('0.1.4')], [installedPlugin])[0]?.installedVersion).toBeUndefined()
  })

  it('keeps product spelling in marketplace titles', () => {
    expect(displayPluginName('github')).toBe('GitHub')
    expect(displayPluginName('aap-job-template')).toBe('AAP Job Template')
  })
})
