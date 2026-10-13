import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ExternalActionNodeForm } from './ExternalActionNodeForm'

const { mockUseQuery } = vi.hoisted(() => ({ mockUseQuery: vi.fn() }))

vi.mock('../../../client', () => ({
  pluginPlatformClient: { useQuery: mockUseQuery },
}))

const action = {
  target_name: 'create-issue',
  qualified_name: 'syntara.github.create-issue',
  display_name: 'Create GitHub issue',
  description: 'Create a GitHub issue.',
  revision_digest: `sha256:${'a'.repeat(64)}`,
  step_revision_id: `syntara.github.create-issue:${'a'.repeat(64)}`,
  runtime_kind: 'platform-runner' as const,
  runtime_abi: 'syntara.http/v1',
  image_digest: `sha256:${'b'.repeat(64)}`,
  input_schema: {
    type: 'object',
    required: ['owner', 'repository', 'title'],
    properties: {
      owner: { type: 'string' },
      repository: { type: 'string' },
      title: { type: 'string' },
    },
  },
  output_schema: { type: 'object' },
  error_schema: { type: 'object' },
}

describe('ExternalActionNodeForm', () => {
  it('submits structured inputs without exposing a runtime or credential field', async () => {
    const onSubmit = vi.fn()
    const user = userEvent.setup()
    render(<ExternalActionNodeForm action={action} onSubmit={onSubmit} />)

    await user.clear(screen.getByRole('textbox', { name: 'Name' }))
    await user.type(screen.getByRole('textbox', { name: 'Name' }), 'Open issue')
    await user.clear(screen.getByRole('textbox', { name: 'Inputs (JSON)' }))
    await user.click(screen.getByRole('textbox', { name: 'Inputs (JSON)' }))
    await user.paste('{"owner":"syntara","repository":"demo","title":"Issue title"}')
    fireEvent.submit(screen.getByRole('form'))

    expect(onSubmit).toHaveBeenCalledWith({
      name: 'Open issue',
      inputs: { owner: 'syntara', repository: 'demo', title: 'Issue title' },
    })
    expect(screen.queryByLabelText(/image/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/credential/i)).not.toBeInTheDocument()
  })

  it('rejects non-object JSON input', async () => {
    const onSubmit = vi.fn()
    const user = userEvent.setup()
    render(<ExternalActionNodeForm action={action} onSubmit={onSubmit} />)

    await user.clear(screen.getByRole('textbox', { name: 'Inputs (JSON)' }))
    await user.click(screen.getByRole('textbox', { name: 'Inputs (JSON)' }))
    await user.paste('[]')
    fireEvent.submit(screen.getByRole('form'))

    expect(onSubmit).not.toHaveBeenCalled()
    expect(screen.getByText('Inputs must be a JSON object.')).toBeInTheDocument()
  })

  it('performs lightweight preflight validation against the installed input contract', async () => {
    const onSubmit = vi.fn()
    const user = userEvent.setup()
    render(<ExternalActionNodeForm action={action} onSubmit={onSubmit} />)

    await user.clear(screen.getByRole('textbox', { name: 'Inputs (JSON)' }))
    await user.click(screen.getByRole('textbox', { name: 'Inputs (JSON)' }))
    await user.paste('{"owner":"syntara","repository":"demo"}')
    fireEvent.submit(screen.getByRole('form'))

    expect(onSubmit).not.toHaveBeenCalled()
    expect(screen.getByText('Missing required field: "title"')).toBeInTheDocument()
  })

  it('requires a matching saved integration when the installed action declares an integration', () => {
    const onSubmit = vi.fn()
    mockUseQuery.mockReturnValue({ data: { resources: [] }, isPending: false })
    render(
      <ExternalActionNodeForm
        action={{ ...action, integration_type_revision_id: 'integration-revision-1' }}
        onSubmit={onSubmit}
        projectId="project-1"
      />
    )

    fireEvent.submit(screen.getByRole('form'))

    expect(onSubmit).not.toHaveBeenCalled()
    expect(screen.getByText('Select an integration for this step.')).toBeInTheDocument()
    expect(mockUseQuery).toHaveBeenCalledWith(
      'get',
      '/plugin_connections',
      {
        params: {
          query: {
            project_id: 'project-1',
            integration_type_revision_id: 'integration-revision-1',
            enabled: true,
          },
        },
      },
      { enabled: true }
    )
  })
})
