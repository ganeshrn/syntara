import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { PluginIntegrationsList } from './PluginIntegrationsList'

const { deleteConnection, mockUseMutation, mockUseQuery, updateConnection } = vi.hoisted(() => ({
  deleteConnection: vi.fn(),
  mockUseMutation: vi.fn(),
  mockUseQuery: vi.fn(),
  updateConnection: vi.fn(),
}))

vi.mock('../../../client', () => ({
  pluginPlatformClient: { useMutation: mockUseMutation, useQuery: mockUseQuery },
}))

vi.mock('../../../hooks/useProjectSelector', () => ({
  useProjectSelector: () => ({
    stableProjectId: '00000000-0000-4000-8000-000000000001',
    ProjectSelector: <div>Default project</div>,
  }),
}))

vi.mock('../../../providers/alerts', () => ({
  useAlerts: () => ({ showAlert: vi.fn() }),
}))

vi.mock('../../../hooks/useMutationErrorHandler', () => ({
  useMutationErrorHandler: () => () => vi.fn(),
}))

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
}))

const contracts = [
  {
    integrationType: {
      id: '00000000-0000-4000-8000-000000000010',
      type_id: 'syntara.github.connection',
      display_name: 'GitHub connection',
      description: 'GitHub connection contract',
      endpoint_kinds: ['github-cloud'],
      credential_type_ids: [],
      configuration_schema: {},
    },
    credentialTypes: [],
    sourcePlugin: {
      namespace: 'syntara',
      name: 'github',
      version: '0.1.4',
      displayName: 'GitHub',
    },
  },
]

function mockConnectionQuery() {
  mockUseQuery.mockReturnValue({
    data: {
      resources: [
        {
          id: '00000000-0000-4000-8000-000000000100',
          name: 'Production GitHub',
          integration_type_revision_id: '00000000-0000-4000-8000-000000000010',
          endpoint_kind: 'github-cloud',
          configuration: { endpointOrigin: 'https://github.com' },
          enabled: true,
          created_at: '2026-10-08T00:00:00Z',
          updated_at: '2026-10-08T00:00:00Z',
        },
      ],
    },
    isPending: false,
    error: undefined,
    refetch: vi.fn(),
  })
}

describe('PluginIntegrationsList', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockConnectionQuery()
    mockUseMutation.mockImplementation((method: string) => ({
      mutate: method === 'patch' ? updateConnection : deleteConnection,
      isPending: false,
    }))
  })

  it('shows saved plugin integrations in an operational table', () => {
    render(<PluginIntegrationsList contracts={contracts} />)

    expect(screen.getByRole('grid', { name: 'Plugin integrations' })).toBeInTheDocument()
    expect(screen.getByText('Production GitHub')).toBeInTheDocument()
    expect(screen.getByText('GitHub connection')).toBeInTheDocument()
    expect(screen.getByText('GitHub 0.1.4')).toBeInTheDocument()
    expect(screen.getByText('github cloud')).toBeInTheDocument()
    expect(screen.getByText('https://github.com')).toBeInTheDocument()
    expect(mockUseQuery).toHaveBeenCalled()
  })

  it('requires confirmation before disabling a plugin integration', async () => {
    const user = userEvent.setup()
    render(<PluginIntegrationsList contracts={contracts} />)

    await user.click(screen.getByRole('switch', { name: 'Toggle Production GitHub' }))
    expect(screen.getByText('Disable plugin integration?')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Disable integration' }))
    expect(updateConnection).toHaveBeenCalledWith(
      {
        params: { path: { connection_id: '00000000-0000-4000-8000-000000000100' } },
        body: { enabled: false },
      },
      expect.any(Object)
    )
  })

  it('requires an acknowledgement before deleting a plugin integration', async () => {
    const user = userEvent.setup()
    render(<PluginIntegrationsList contracts={contracts} />)

    await user.click(screen.getByRole('button', { name: 'Actions for Production GitHub' }))
    await user.click(screen.getByText('Delete integration'))
    expect(screen.getByText('Delete plugin integration?')).toBeInTheDocument()

    const deleteButton = screen.getByRole('button', { name: 'Delete integration' })
    expect(deleteButton).toBeDisabled()
    await user.click(screen.getByRole('checkbox'))
    await user.click(deleteButton)

    expect(deleteConnection).toHaveBeenCalledWith(
      { params: { path: { connection_id: '00000000-0000-4000-8000-000000000100' } } },
      expect.any(Object)
    )
  })
})
