import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { axe } from 'vitest-axe'

import { MockLink } from '../../../test/setup'

import { PluginDetail } from './PluginDetail'

const { mockNavigate, mockUseMutation, mockUseQuery, mutationSpies } = vi.hoisted(() => ({
  mockNavigate: vi.fn(),
  mockUseMutation: vi.fn(),
  mockUseQuery: vi.fn(),
  mutationSpies: { install: vi.fn(), hydrate: vi.fn() },
}))

vi.mock('@tanstack/react-router', async () => {
  const actual = await vi.importActual<typeof import('@tanstack/react-router')>('@tanstack/react-router')
  return {
    ...actual,
    Link: MockLink,
    useNavigate: () => mockNavigate,
    useParams: () => ({ sourceId: 'local-quay', namespace: 'syntara', pluginName: 'github' }),
  }
})

vi.mock('../../../client', () => ({
  pluginPlatformClient: { useQuery: mockUseQuery, useMutation: mockUseMutation },
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
    if (path === '/catalog/plugins') return queryResult({ resources: [plugin] })
    if (path === '/plugin_registry') {
      return queryResult({
        resources: [
          {
            id: 'installed-1',
            namespace: 'syntara',
            name: 'github',
            version: '0.1.4',
            display_name: 'GitHub',
            description: 'GitHub workflow actions',
            artifact_repository: 'syntara-admin/plugins/github',
            artifact_digest: 'sha256:plugin',
            action_count: 1,
            enabled: true,
            installed_at: '2026-10-08T03:31:36Z',
          },
        ],
      })
    }
    if (path === '/plugin_registry/{namespace}/{name}/versions/{version}') {
      return queryResult({
        actions: [
          {
            step_revision_id: 'step-1',
            display_name: 'Create issue',
            description: 'Create a GitHub issue.',
            input_schema: {
              type: 'object',
              required: ['owner', 'repository', 'title'],
              properties: {
                owner: { type: 'string', description: 'Repository owner.' },
                repository: { type: 'string', description: 'Repository name.' },
                title: { type: 'string', description: 'Issue title.' },
                body: { type: 'string', description: 'Optional issue body.' },
              },
            },
          },
        ],
        integration_types: [
          {
            id: 'integration-type-1',
            type_id: 'syntara.github.connection',
            display_name: 'GitHub connection',
            description: 'A GitHub Cloud or Enterprise Server integration.',
            endpoint_kinds: ['github-cloud'],
            credential_type_ids: ['syntara.github.service-token'],
            configuration_schema: { type: 'object' },
          },
        ],
        credential_types: [],
      })
    }
    if (path === '/catalog/plugins/{namespace}/{name}/versions/{version}/documentation') {
      return queryResult({ ...plugin, rendered_html: '<h3>GitHub plugin documentation</h3><p>Verified content.</p>' })
    }
    throw new Error(`Unexpected query path: ${path}`)
  })
  mockUseMutation.mockImplementation((_method: string, path: string) => ({
    mutate: path.endsWith('/documentation/hydrate') ? mutationSpies.hydrate : mutationSpies.install,
    isPending: false,
  }))
}

describe('PluginDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    configureClientMocks()
  })

  it('shows entity metadata, installed workflow steps, documentation, and verification', async () => {
    const { container } = render(<PluginDetail />)

    expect(screen.getByRole('heading', { name: 'GitHub' })).toBeInTheDocument()
    expect(screen.getByText('Installed 0.1.4')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Workflow steps' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Integration setup' })).toBeInTheDocument()
    expect(screen.getByText('GitHub connection')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Configure plugin integrations' })).toBeInTheDocument()
    expect(screen.getByText('Create issue')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Input contract' })).toBeInTheDocument()
    expect(screen.getByText('owner')).toBeInTheDocument()
    expect(screen.getAllByText('Required')).toHaveLength(3)
    expect(screen.getByText('Optional')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'GitHub plugin documentation' })).toBeInTheDocument()
    expect(screen.getByText('sha256:plugin')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Install again' })).toBeInTheDocument()
    expect(await axe(container)).toHaveNoViolations()
  })

  it('installs the selected source-qualified immutable release', async () => {
    const user = userEvent.setup()
    render(<PluginDetail />)

    await user.click(screen.getByRole('button', { name: 'Install again' }))

    expect(mutationSpies.install).toHaveBeenCalledWith(
      {
        params: {
          path: { namespace: 'syntara', name: 'github', version: '0.1.4' },
          query: { source_id: 'local-quay' },
        },
      },
      expect.any(Object)
    )
  })
})
