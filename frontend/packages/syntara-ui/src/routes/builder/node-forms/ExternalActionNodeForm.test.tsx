import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { axe } from 'vitest-axe'

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
      body: { type: 'string' },
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
    await user.type(screen.getByRole('textbox', { name: /owner/i }), 'syntara')
    await user.type(screen.getByRole('textbox', { name: /repository/i }), 'demo')
    await user.type(screen.getByRole('textbox', { name: /title/i }), 'Issue title')
    fireEvent.submit(screen.getByRole('form'))

    expect(onSubmit).toHaveBeenCalledWith({
      name: 'Open issue',
      inputs: { owner: 'syntara', repository: 'demo', title: 'Issue title' },
    })
    expect(screen.queryByLabelText(/image/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/credential/i)).not.toBeInTheDocument()
  })

  it('performs lightweight preflight validation against the installed input contract', async () => {
    const onSubmit = vi.fn()
    const user = userEvent.setup()
    render(<ExternalActionNodeForm action={action} onSubmit={onSubmit} />)

    await user.type(screen.getByRole('textbox', { name: /owner/i }), 'syntara')
    await user.type(screen.getByRole('textbox', { name: /repository/i }), 'demo')
    fireEvent.submit(screen.getByRole('form'))

    expect(onSubmit).not.toHaveBeenCalled()
    expect(screen.getByText('Missing required field: "title"')).toBeInTheDocument()
  })

  it('applies the installed native RJSF uiSchema', () => {
    render(
      <ExternalActionNodeForm
        action={{
          ...action,
          input_presentation: {
            renderer: 'rjsf.v1',
            uiSchema: { body: { 'ui:widget': 'textarea' } },
          },
        }}
        onSubmit={vi.fn()}
      />
    )

    expect(screen.getByRole('textbox', { name: /body/i }).tagName).toBe('TEXTAREA')
  })

  it('renders the JSON Schema branch selected by current input data', () => {
    const conditionalAction = {
      ...action,
      input_schema: {
        type: 'object',
        properties: {
          request_type: { type: 'string', enum: ['bug', 'feature'] },
        },
        if: {
          properties: { request_type: { const: 'bug' } },
          required: ['request_type'],
        },
        then: {
          properties: { reproduction_steps: { type: 'string', title: 'Reproduction steps' } },
          required: ['reproduction_steps'],
        },
        else: {
          properties: { business_value: { type: 'string', title: 'Business value' } },
          required: ['business_value'],
        },
      },
    }
    const { unmount } = render(
      <ExternalActionNodeForm
        action={conditionalAction}
        initialData={{ inputs: { request_type: 'bug' } }}
        onSubmit={vi.fn()}
      />
    )

    expect(screen.getByRole('textbox', { name: /reproduction steps/i })).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: /business value/i })).not.toBeInTheDocument()

    unmount()
    render(
      <ExternalActionNodeForm
        action={conditionalAction}
        initialData={{ inputs: { request_type: 'feature' } }}
        onSubmit={vi.fn()}
      />
    )

    expect(screen.getByRole('textbox', { name: /business value/i })).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: /reproduction steps/i })).not.toBeInTheDocument()
  })

  it('renders conditional HTTP body and numeric timeout fields together', () => {
    expect(() =>
      render(
        <ExternalActionNodeForm
          action={{
            ...action,
            input_schema: {
              type: 'object',
              properties: {
                method: { type: 'string', enum: ['GET', 'POST'] },
                timeout_seconds: { type: 'number', default: 30 },
              },
              if: { properties: { method: { const: 'POST' } }, required: ['method'] },
              then: {
                properties: {
                  body: {
                    oneOf: [{ type: 'object' }, { type: 'string' }],
                  },
                },
              },
            },
            input_presentation: {
              renderer: 'rjsf.v1',
              uiSchema: {},
            },
          }}
          initialData={{ inputs: { method: 'POST' } }}
          onSubmit={vi.fn()}
        />
      )
    ).not.toThrow()
  })

  it('has no detectable accessibility violations', async () => {
    const { container } = render(<ExternalActionNodeForm action={action} onSubmit={vi.fn()} />)

    expect(await axe(container)).toHaveNoViolations()
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
