import type { PluginPlatformAPI } from '@syntara/contracts'

export type CatalogPlugin = PluginPlatformAPI.components['schemas']['CatalogPluginRead']
export type InstalledPlugin = PluginPlatformAPI.components['schemas']['InstalledPluginRead']
export type PluginSource = PluginPlatformAPI.components['schemas']['PluginSourceRead']

export type PluginCard = Readonly<{
  sourceId: string
  namespace: string
  name: string
  versions: readonly CatalogPlugin[]
  selectedVersion: CatalogPlugin
  installedVersion: InstalledPlugin | undefined
}>

type ParsedVersion = Readonly<{
  major: number
  minor: number
  patch: number
  prerelease: string | undefined
}>

function parseVersion(value: string): ParsedVersion | undefined {
  const match = /^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$/.exec(value)
  if (!match) return undefined

  return {
    major: Number(match[1]),
    minor: Number(match[2]),
    patch: Number(match[3]),
    prerelease: match[4],
  }
}

/** Compare supported plugin semantic versions without adding a client dependency. */
export function comparePluginVersions(left: string, right: string): number {
  const a = parseVersion(left)
  const b = parseVersion(right)
  if (!a || !b) return left.localeCompare(right)

  for (const part of ['major', 'minor', 'patch'] as const) {
    if (a[part] !== b[part]) return a[part] - b[part]
  }
  if (a.prerelease === b.prerelease) return 0
  if (a.prerelease === undefined) return 1
  if (b.prerelease === undefined) return -1
  return a.prerelease.localeCompare(b.prerelease)
}

function identity(sourceId: string, namespace: string, name: string): string {
  return `${sourceId}:${namespace}/${name}`
}

/**
 * Marketplace cards represent a plugin, not an individual release. Releases
 * remain visible in the entity inspection page where their differences matter.
 */
export function groupCatalogPlugins(
  plugins: readonly CatalogPlugin[],
  installedPlugins: readonly InstalledPlugin[]
): readonly PluginCard[] {
  const groups = new Map<string, CatalogPlugin[]>()

  for (const plugin of plugins) {
    const key = identity(plugin.source_id, plugin.namespace, plugin.name)
    groups.set(key, [...(groups.get(key) ?? []), plugin])
  }

  return [...groups.values()]
    .map((versions) => {
      const sortedVersions = [...versions].sort((a, b) => comparePluginVersions(b.version, a.version))
      const selectedVersion = sortedVersions[0]
      const installedVersion = installedPlugins.find(
        (plugin) =>
          plugin.namespace === selectedVersion.namespace &&
          plugin.name === selectedVersion.name &&
          sortedVersions.some((version) => version.version === plugin.version)
      )

      return {
        sourceId: selectedVersion.source_id,
        namespace: selectedVersion.namespace,
        name: selectedVersion.name,
        versions: sortedVersions,
        selectedVersion,
        installedVersion,
      }
    })
    .sort((a, b) => `${a.namespace}/${a.name}`.localeCompare(`${b.namespace}/${b.name}`))
}

export function displayPluginName(name: string): string {
  const productNames: Readonly<Record<string, string>> = {
    aap: 'AAP',
    github: 'GitHub',
  }

  return name
    .split('-')
    .filter(Boolean)
    .map((part) => productNames[part.toLowerCase()] ?? `${part[0]?.toUpperCase() ?? ''}${part.slice(1)}`)
    .join(' ')
}
