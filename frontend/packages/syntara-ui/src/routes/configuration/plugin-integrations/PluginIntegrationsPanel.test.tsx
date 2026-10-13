import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { PluginIntegrationsPanel } from './PluginIntegrationsPanel'

const { mockUseMutation, mockUseQuery } = vi.hoisted(() => ({
  mockUseMutation: vi.fn(),
  mockUseQuery: vi.fn(),
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
  useAlerts: () => ({ showSuccess: vi.fn() }),
}))

vi.mock('../../../hooks/useMutationErrorHandler', () => ({
  useMutationErrorHandler: () => () => vi.fn(),
}))

const integration = {
  id: '00000000-0000-4000-8000-000000000010',
  type_id: 'syntara.github.connection',
  display_name: 'GitHub connection',
  description: 'A GitHub Cloud or Enterprise Server connection.',
  endpoint_kinds: ['github-cloud', 'github-enterprise'],
  credential_type_ids: ['syntara.github.service-token'],
  configuration_schema: {
    type: 'object',
    required: ['endpointKind'],
    properties: {
      endpointKind: { type: 'string', enum: ['github-cloud', 'github-enterprise'] },
      endpointOrigin: { type: 'string', title: 'Endpoint origin' },
    },
  },
}

const credential = {
  id: '00000000-0000-4000-8000-000000000011',
  integration_type_revision_id: integration.id,
  type_id: 'syntara.github.service-token',
  display_name: 'GitHub service token',
  description: 'A static bearer token.',
  credential_schema: {
    type: 'object',
    required: ['token'],
    properties: { token: { type: 'string', title: 'Token' } },
  },
  secret_field_names: ['token'],
  issuance: { kind: 'static-bearer.v1' },
}

describe('PluginIntegrationsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockUseQuery.mockReturnValue({ data: { resources: [] }, isPending: false, refetch: vi.fn() })
    mockUseMutation.mockReturnValue({ mutate: vi.fn(), isPending: false })
  })

  it('renders installed schemas and treats declared credential fields as passwords', () => {
    render(
      <PluginIntegrationsPanel
        integrationTypes={[integration]}
        credentialTypes={[credential]}
        initialIntegrationTypeId={integration.id}
        sourcePluginByIntegrationTypeId={new Map([[integration.id, 'GitHub']])}
      />
    )

    expect(screen.getByRole('heading', { name: 'Plugin integrations' })).toBeInTheDocument()
    expect(screen.getByText('Default project')).toBeInTheDocument()
    expect(screen.getByLabelText('Endpoint origin')).toBeInTheDocument()
    expect(screen.getByLabelText('Token')).toHaveAttribute('type', 'password')
    expect(screen.getByRole('button', { name: 'plugin-integration-type' })).toHaveTextContent('Plugin: GitHub')
    expect(screen.getByText(/does not yet deliver credentials to the Execution Plane/i)).toBeInTheDocument()
    expect(mockUseQuery).toHaveBeenCalledWith(
      'get',
      '/plugin_connections',
      {
        params: {
          query: {
            project_id: '00000000-0000-4000-8000-000000000001',
            integration_type_revision_id: integration.id,
            enabled: true,
            include_total: true,
          },
        },
      },
      { enabled: true }
    )
  })

  it('does not render anything when the installed plugin has no integration contract', () => {
    const { container } = render(<PluginIntegrationsPanel integrationTypes={[]} credentialTypes={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('omits a blank optional URI field before provisioning the integration', async () => {
    const user = userEvent.setup()
    const mutate = vi.fn()
    mockUseMutation.mockReturnValue({ mutate, isPending: false })
    render(<PluginIntegrationsPanel integrationTypes={[integration]} credentialTypes={[credential]} />)

    await user.type(screen.getByRole('textbox', { name: /integration name/i }), 'GitHub Cloud')
    await user.click(screen.getByRole('button', { name: 'plugin-integration-endpoint' }))
    await user.click(screen.getByRole('option', { name: 'Github cloud' }))
    await user.type(screen.getByLabelText('Token'), 'not-a-real-token')
    await user.click(screen.getByRole('button', { name: 'Save integration' }))

    expect(mutate).toHaveBeenCalledWith(
      {
        body: {
          name: 'GitHub Cloud',
          project_id: '00000000-0000-4000-8000-000000000001',
          integration_type_revision_id: integration.id,
          credential_type_revision_id: credential.id,
          endpoint_kind: 'github-cloud',
          configuration: { endpointKind: 'github-cloud' },
          credential_inputs: { token: 'not-a-real-token' },
        },
      },
      expect.any(Object)
    )
  })
})
