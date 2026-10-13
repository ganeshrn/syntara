import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { axe } from 'vitest-axe'

import { expectPageTitle } from '../../../test/pageTitle'

import { Plugins } from './Plugins'

const { mockNavigate, mockUseQuery, mockUseMutation, mutationSpies } = vi.hoisted(() => ({
  mockNavigate: vi.fn(),
  mockUseQuery: vi.fn(),
  mockUseMutation: vi.fn(),
  mutationSpies: {
    createSource: vi.fn(),
    reconcile: vi.fn(),
    hydrate: vi.fn(),
    install: vi.fn(),
  },
}))

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
}))

vi.mock('../../../client', () => ({
  pluginPlatformClient: {
    useQuery: mockUseQuery,
    useMutation: mockUseMutation,
  },
}))

vi.mock('../../../hooks/useCanI', () => ({
  useCanI: () => ({ allowed: true, isChecking: false, isError: false }),
}))

vi.mock('../../../providers/alerts', () => ({
  useAlerts: () => ({ showSuccess: vi.fn() }),
}))

vi.mock('../../../hooks/useMutationErrorHandler', () => ({
  useMutationErrorHandler: () => () => vi.fn(),
}))

vi.mock('../../../utils/docs/useDocLink', () => ({
  useDocLink: () => 'https://docs.example/plugins',
}))

const source = {
  id: 'source-record-1',
  source_id: 'local-quay',
  display_name: 'Local Quay plugin catalog',
  source_mode: 'local-poc' as const,
  registry_origin: 'http://localhost:8443',
  index_repository: 'syntara-admin/catalog-index',
  channel: 'stable',
  repository_scopes: ['syntara-admin/plugins'],
  credential_ref: 'development/local-poc-anonymous',
  trust_policy_ref: 'development/local-poc-unsigned',
  custom_ca_ref: null,
  mirror_policy: 'direct' as const,
  reconciliation_interval_seconds: 300,
  enabled: true,
  active_reconciliation_id: null,
  created_at: '2026-10-08T03:29:05Z',
  updated_at: '2026-10-08T03:29:05Z',
}

const plugin = {
  id: 'catalog-plugin-1',
  source_id: 'local-quay',
  reconciliation_id: 'reconciliation-1',
  namespace: 'syntara',
  name: 'github',
  version: '0.1.4',
  artifact_repository: 'syntara-admin/plugins/github',
  artifact_digest: 'sha256:plugin',
  documentation_digest: 'sha256:documentation',
  runtime_ownerships: ['platform-runner'] as const,
  deprecated: false,
  withdrawn: false,
  created_at: '2026-10-08T03:30:36Z',
  updated_at: '2026-10-08T03:30:36Z',
}

function queryResult(data: unknown) {
  return { data, error: null, isPending: false, refetch: vi.fn() }
}

function configureClientMocks() {
  mockUseQuery.mockImplementation((_method: string, path: string) => {
    if (path === '/plugin_sources') return queryResult({ resources: [source] })
    if (path === '/catalog/plugins') return queryResult({ resources: [plugin] })
    if (path === '/plugin_registry') return queryResult({ resources: [] })
    if (path === '/catalog/plugins/{namespace}/{name}/versions/{version}/documentation') {
      return queryResult({
        ...plugin,
        rendered_html: '<h1>GitHub</h1><p>Create issues through a managed runner.</p>',
        rendered_at: '2026-10-08T03:33:29Z',
      })
    }
    if (path === '/plugin_registry/{namespace}/{name}/versions/{version}') {
      return queryResult(undefined)
    }
    throw new Error(`Unexpected query path: ${path}`)
  })

  mockUseMutation.mockImplementation((_method: string, path: string) => {
    let mutate = mutationSpies.install
    if (path === '/plugin_sources') {
      mutate = mutationSpies.createSource
    } else if (path === '/plugin_sources/{plugin_source_id}/reconcile') {
      mutate = mutationSpies.reconcile
    } else if (path === '/catalog/plugins/{namespace}/{name}/versions/{version}/documentation/hydrate') {
      mutate = mutationSpies.hydrate
    }
    return { mutate, isPending: false }
  })
}

describe('Plugins', () => {
  beforeAll(() => {
    HTMLElement.prototype.scrollIntoView = vi.fn()
  })

  afterAll(() => {
    vi.restoreAllMocks()
  })

  beforeEach(() => {
    vi.clearAllMocks()
    configureClientMocks()
  })

  it('shows an administrator the catalog source and discovered marketplace plugin', async () => {
    const { container } = render(<Plugins />)

    expectPageTitle(['Plugins'])
    expect(screen.getByRole('heading', { name: 'Catalog sources' })).toBeInTheDocument()
    expect(screen.getByText('Local Quay plugin catalog')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Marketplace' })).toBeInTheDocument()
    expect(screen.getByText('syntara/github')).toBeInTheDocument()
    expect(screen.getByText('0.1.4')).toBeInTheDocument()
    expect(await axe(container)).toHaveNoViolations()
  })

  it('navigates to the plugin entity page', async () => {
    const user = userEvent.setup()
    render(<Plugins />)

    await user.click(screen.getByRole('button', { name: 'Inspect plugin' }))

    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/configuration/plugins/$sourceId/$namespace/$pluginName',
      params: { sourceId: 'local-quay', namespace: 'syntara', pluginName: 'github' },
    })
  })

  it('opens the local catalog form with safe development defaults', async () => {
    const user = userEvent.setup()
    render(<Plugins />)

    await user.click(screen.getByRole('button', { name: 'Add local catalog' }))

    expect(screen.getByRole('heading', { name: /Add local plugin catalog/ })).toBeInTheDocument()
    expect(screen.getByDisplayValue('http://localhost:8443')).toBeInTheDocument()
    expect(screen.getByDisplayValue('syntara-admin/catalog-index')).toBeInTheDocument()
    expect(screen.getByText(/does not put a registry password/i)).toBeInTheDocument()
  })

  it('submits only the local catalog configuration, not a registry secret', async () => {
    const user = userEvent.setup()
    render(<Plugins />)

    await user.click(screen.getByRole('button', { name: 'Add local catalog' }))
    await user.click(screen.getByRole('button', { name: 'Add catalog' }))

    expect(mutationSpies.createSource).toHaveBeenCalledWith(
      {
        body: {
          source_id: 'local-quay',
          display_name: 'Local Quay plugin catalog',
          source_mode: 'local-poc',
          registry_origin: 'http://localhost:8443',
          index_repository: 'syntara-admin/catalog-index',
          channel: 'stable',
          repository_scopes: ['syntara-admin/plugins'],
          credential_ref: 'development/local-poc-anonymous',
          trust_policy_ref: 'development/local-poc-unsigned',
          mirror_policy: 'direct',
          reconciliation_interval_seconds: 300,
          enabled: true,
        },
      },
      expect.any(Object)
    )
  })

  it('requests a catalog refresh for the selected source', async () => {
    const user = userEvent.setup()
    render(<Plugins />)

    await user.click(screen.getByRole('button', { name: 'Refresh catalog' }))

    await waitFor(() => {
      expect(mutationSpies.reconcile).toHaveBeenCalledWith(
        { params: { path: { plugin_source_id: 'source-record-1' } } },
        expect.any(Object)
      )
    })
  })
})
