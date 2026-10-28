import {
  Content,
  ContentVariants,
  FormGroup,
  FormHelperText,
  HelperText,
  HelperTextItem,
  MenuToggle,
  type MenuToggleElement,
  SelectList,
  SelectOption,
  TextInput,
} from '@patternfly/react-core'
import type { RJSFSchema, UiSchema } from '@rjsf/utils'
import type { PluginPlatformAPI } from '@syntara/contracts'
import { useState, type FormEvent, type Ref } from 'react'

import { pluginPlatformClient } from '../../../client'
import { SynSelect } from '../../../components/SynSelect'
import { validateJsonSchemaObject } from '../../../utils/jsonSchemaValidation'

import { NodeFormContainer } from './shared/NodeFormContainer'
import { SchemaInputFields } from './shared/SchemaInputFields'

export type InstalledPluginAction = PluginPlatformAPI.components['schemas']['InstalledPluginActionRead']

export type ExternalActionFormData = {
  name: string
  inputs: Record<string, unknown>
  pluginIntegrationId?: string
}

type ExternalActionNodeFormProps = {
  action?: InstalledPluginAction
  initialData?: Partial<ExternalActionFormData>
  onSubmit: (data: ExternalActionFormData) => void
  projectId?: string
}

function PluginIntegrationSelector({
  projectId,
  integrationTypeRevisionId,
  value,
  onChange,
}: Readonly<{
  projectId?: string
  integrationTypeRevisionId: string
  value?: string
  onChange: (integrationId: string | undefined) => void
}>) {
  const [isOpen, setIsOpen] = useState(false)
  const integrationsQuery = pluginPlatformClient.useQuery(
    'get',
    '/plugin_connections',
    {
      params: {
        query: {
          project_id: projectId ?? '00000000-0000-0000-0000-000000000000',
          integration_type_revision_id: integrationTypeRevisionId,
          enabled: true,
        },
      },
    },
    { enabled: Boolean(projectId) }
  )
  const integrations = integrationsQuery.data?.resources ?? []

  if (!projectId) {
    return (
      <FormGroup label="Integration" fieldId="external-action-integration" isRequired>
        <Content component={ContentVariants.small}>
          Select a workflow project before choosing this action’s integration.
        </Content>
      </FormGroup>
    )
  }

  return (
    <FormGroup label="Integration" fieldId="external-action-integration" isRequired>
      <SynSelect
        id="external-action-integration"
        isOpen={isOpen}
        selected={value}
        onSelect={(_event, selected) => {
          onChange(String(selected) || undefined)
          setIsOpen(false)
        }}
        onOpenChange={setIsOpen}
        toggle={(toggleRef: Ref<MenuToggleElement>) => (
          <MenuToggle
            ref={toggleRef}
            onClick={() => setIsOpen((current) => !current)}
            isExpanded={isOpen}
            isDisabled={integrationsQuery.isPending}
            isFullWidth
            isPlaceholder={!value}
            aria-label="Integration"
          >
            {integrations.find((integration) => integration.id === value)?.name ?? 'Select an integration'}
          </MenuToggle>
        )}
      >
        <SelectList>
          {integrations.map((integration) => (
            <SelectOption key={integration.id} value={integration.id} isSelected={integration.id === value}>
              {integration.name} ({integration.endpoint_kind})
            </SelectOption>
          ))}
        </SelectList>
      </SynSelect>
      <FormHelperText>
        <HelperText>
          <HelperTextItem variant="default">
            {integrations.length > 0
              ? 'The workflow stores the selected integration identity, not its credential value.'
              : 'No compatible integration exists for this project. Configure one under Plugin integrations first.'}
          </HelperTextItem>
        </HelperText>
      </FormHelperText>
    </FormGroup>
  )
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isRjsfSchema(value: unknown): value is RJSFSchema {
  return isObject(value)
}

function inputUiSchema(action: InstalledPluginAction): UiSchema | undefined {
  const presentation = action.input_presentation
  if (!isObject(presentation) || presentation.renderer !== 'rjsf.v1') return undefined
  return isObject(presentation.uiSchema) ? presentation.uiSchema : undefined
}

export function ExternalActionNodeForm({ action, initialData, onSubmit, projectId }: ExternalActionNodeFormProps) {
  const [name, setName] = useState(initialData?.name ?? action?.display_name ?? '')
  const [inputs, setInputs] = useState<Record<string, unknown>>(initialData?.inputs ?? {})
  const [pluginIntegrationId, setPluginIntegrationId] = useState(initialData?.pluginIntegrationId)
  const [error, setError] = useState<string | undefined>()

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!action) {
      setError('The installed action definition is unavailable.')
      return
    }
    if (!name.trim()) {
      setError('A step name is required.')
      return
    }
    if (action.integration_type_revision_id && !pluginIntegrationId) {
      setError('Select an integration for this step.')
      return
    }
    const validationErrors = validateJsonSchemaObject(inputs, action.input_schema)
    if (validationErrors.length > 0) {
      setError(validationErrors[0])
      return
    }
    onSubmit({
      name: name.trim(),
      inputs,
      ...(action.integration_type_revision_id ? { pluginIntegrationId } : {}),
    })
  }

  if (!action) {
    return <Content component={ContentVariants.p}>The installed action definition is unavailable.</Content>
  }

  return (
    <NodeFormContainer formId="external-action-node-form" onSubmit={submit}>
      <Content component={ContentVariants.p} style={{ marginBottom: 'var(--pf-t--global--spacer--md)' }}>
        {action.description}
      </Content>
      <Content component={ContentVariants.small} style={{ marginBottom: 'var(--pf-t--global--spacer--md)' }}>
        This workflow stores the approved action revision, inputs, and selected integration identity only. Credential
        values remain in Syntara’s encrypted secret store.
      </Content>
      <FormGroup label="Name" fieldId="external-action-name" isRequired>
        <TextInput
          id="external-action-name"
          aria-label="Name"
          value={name}
          onChange={(_event, value) => setName(value)}
        />
      </FormGroup>
      {action.integration_type_revision_id ? (
        <PluginIntegrationSelector
          projectId={projectId}
          integrationTypeRevisionId={action.integration_type_revision_id}
          value={pluginIntegrationId}
          onChange={(value) => {
            setPluginIntegrationId(value)
            setError(undefined)
          }}
        />
      ) : null}
      {isRjsfSchema(action.input_schema) ? (
        <SchemaInputFields
          schema={action.input_schema}
          uiSchema={inputUiSchema(action)}
          value={inputs}
          onChange={(value) => {
            setInputs(value)
            setError(undefined)
          }}
        />
      ) : null}
      {error ? (
        <FormHelperText>
          <HelperText>
            <HelperTextItem variant="error">{error}</HelperTextItem>
          </HelperText>
        </FormHelperText>
      ) : null}
    </NodeFormContainer>
  )
}
