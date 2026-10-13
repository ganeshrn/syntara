import {
  Button,
  Content,
  Form,
  FormGroup,
  FormHelperText,
  HelperText,
  HelperTextItem,
  List,
  ListItem,
  MenuToggle,
  type MenuToggleElement,
  SelectList,
  SelectOption,
  TextInput,
  Title,
} from '@patternfly/react-core'
import type { PluginPlatformAPI } from '@syntara/contracts'
import { useState, type FormEvent, type Ref } from 'react'

import { pluginPlatformClient } from '../../../client'
import { SynPanel } from '../../../components/layout/SynPanel'
import { SynSelect } from '../../../components/SynSelect'
import { useMutationErrorHandler } from '../../../hooks/useMutationErrorHandler'
import { useProjectSelector } from '../../../hooks/useProjectSelector'
import { useAlerts } from '../../../providers/alerts'
import { detachPromise } from '../../../utils/detachPromise'

type IntegrationType = PluginPlatformAPI.components['schemas']['InstalledPluginIntegrationTypeRead']
type CredentialType = PluginPlatformAPI.components['schemas']['InstalledPluginCredentialTypeRead']

type SchemaField = Readonly<{
  name: string
  title: string
  enumValues: readonly string[]
  description?: string
  isRequired: boolean
}>

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined
}

function humanize(name: string): string {
  return name
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/[-_]/g, ' ')
    .replace(/^./, (value) => value.toUpperCase())
}

function schemaFields(schema: Record<string, unknown> | undefined): readonly SchemaField[] {
  const properties = asRecord(schema?.properties)
  if (!properties) return []
  const requiredFieldNames = new Set(
    Array.isArray(schema?.required) ? schema.required.filter((item): item is string => typeof item === 'string') : []
  )
  return Object.entries(properties).flatMap(([name, value]) => {
    const property = asRecord(value)
    if (!property) return []
    const enumValues = Array.isArray(property.enum)
      ? property.enum.filter((item): item is string => typeof item === 'string')
      : []
    return [
      {
        name,
        title: typeof property.title === 'string' ? property.title : humanize(name),
        enumValues,
        description: typeof property.description === 'string' ? property.description : undefined,
        isRequired: requiredFieldNames.has(name),
      },
    ]
  })
}

function inputValue(values: Record<string, string>, name: string): string {
  return values[name] ?? ''
}

/** Do not send an empty optional string to a schema that constrains its format. */
function populatedValues(values: Record<string, string>): Record<string, string> {
  return Object.fromEntries(
    Object.entries(values).flatMap(([name, value]) => {
      const normalized = value.trim()
      return normalized ? [[name, normalized]] : []
    })
  )
}

function missingRequiredFields(fields: readonly SchemaField[], values: Record<string, string>): readonly SchemaField[] {
  return fields.filter((field) => field.isRequired && !inputValue(values, field.name).trim())
}

function ValueSelect({
  id,
  value,
  options,
  placeholder,
  onChange,
  isDisabled = false,
}: Readonly<{
  id: string
  value: string
  options: readonly Readonly<{ value: string; label: string }>[]
  placeholder: string
  onChange: (value: string) => void
  isDisabled?: boolean
}>) {
  const [isOpen, setIsOpen] = useState(false)
  const selectedLabel = options.find((option) => option.value === value)?.label
  return (
    <SynSelect
      id={id}
      isOpen={isOpen}
      selected={value || undefined}
      onSelect={(_event, selected) => {
        onChange(String(selected))
        setIsOpen(false)
      }}
      onOpenChange={setIsOpen}
      toggle={(toggleRef: Ref<MenuToggleElement>) => (
        <MenuToggle
          ref={toggleRef}
          onClick={() => setIsOpen((current) => !current)}
          isExpanded={isOpen}
          isDisabled={isDisabled}
          isFullWidth
          isPlaceholder={!value}
          aria-label={id}
        >
          {selectedLabel ?? placeholder}
        </MenuToggle>
      )}
    >
      <SelectList>
        {options.map((option) => (
          <SelectOption key={option.value} value={option.value} isSelected={option.value === value}>
            {option.label}
          </SelectOption>
        ))}
      </SelectList>
    </SynSelect>
  )
}

function SavedPluginIntegrationNames({
  connections,
}: Readonly<{
  connections: readonly Readonly<{ id: string; name: string; endpoint_kind: string }>[]
}>) {
  if (connections.length === 0) return null
  return (
    <>
      <Title headingLevel="h3" size="lg">
        Saved integrations
      </Title>
      <List>
        {connections.map((connection) => (
          <ListItem key={connection.id}>
            <strong>{connection.name}</strong> ({humanize(connection.endpoint_kind)})
          </ListItem>
        ))}
      </List>
    </>
  )
}

function SchemaFieldInputs({
  fields,
  values,
  idPrefix,
  onChange,
  selectEnumValues = false,
  secretFieldNames = [],
}: Readonly<{
  fields: readonly SchemaField[]
  values: Record<string, string>
  idPrefix: string
  onChange: (name: string, value: string) => void
  selectEnumValues?: boolean
  secretFieldNames?: readonly string[]
}>) {
  return fields.map((field) => (
    <FormGroup key={field.name} label={field.title} fieldId={`${idPrefix}-${field.name}`} isRequired={field.isRequired}>
      {selectEnumValues && field.enumValues.length > 0 ? (
        <ValueSelect
          id={`${idPrefix}-${field.name}`}
          value={inputValue(values, field.name)}
          options={field.enumValues.map((value) => ({ value, label: humanize(value) }))}
          placeholder={`Select ${field.title.toLowerCase()}`}
          onChange={(value) => onChange(field.name, value)}
        />
      ) : (
        <TextInput
          id={`${idPrefix}-${field.name}`}
          aria-label={field.title}
          type={secretFieldNames.includes(field.name) ? 'password' : 'text'}
          value={inputValue(values, field.name)}
          autoComplete="off"
          onChange={(_event, value) => onChange(field.name, value)}
        />
      )}
      {field.description ? (
        <FormHelperText>
          <HelperText>
            <HelperTextItem>{field.description}</HelperTextItem>
          </HelperText>
        </FormHelperText>
      ) : null}
    </FormGroup>
  ))
}

/**
 * Setup surface for installed declarative plugin integration contracts.
 *
 * The component renders scalar fields from the plugin-owned JSON Schemas, but
 * connection setup itself remains Syntara-owned: a credential value is sent
 * once to the backend and never rendered, listed, or stored in browser state
 * beyond this open form.
 */
export function PluginIntegrationsPanel({
  integrationTypes,
  credentialTypes,
  sourcePluginByIntegrationTypeId = new Map(),
  initialIntegrationTypeId,
}: Readonly<{
  integrationTypes: readonly IntegrationType[]
  credentialTypes: readonly CredentialType[]
  sourcePluginByIntegrationTypeId?: ReadonlyMap<string, string>
  initialIntegrationTypeId?: string
}>) {
  if (integrationTypes.length === 0) return null
  return (
    <PluginIntegrationsPanelContent
      integrationTypes={integrationTypes}
      credentialTypes={credentialTypes}
      sourcePluginByIntegrationTypeId={sourcePluginByIntegrationTypeId}
      initialIntegrationTypeId={initialIntegrationTypeId}
    />
  )
}

function PluginIntegrationsPanelContent({
  integrationTypes,
  credentialTypes,
  sourcePluginByIntegrationTypeId,
  initialIntegrationTypeId,
}: Readonly<{
  integrationTypes: readonly IntegrationType[]
  credentialTypes: readonly CredentialType[]
  sourcePluginByIntegrationTypeId: ReadonlyMap<string, string>
  initialIntegrationTypeId?: string
}>) {
  const { stableProjectId, ProjectSelector } = useProjectSelector({ requireProject: true })
  const { showSuccess } = useAlerts()
  const handleMutationError = useMutationErrorHandler()
  const [name, setName] = useState('')
  const [selectedIntegrationId, setSelectedIntegrationId] = useState<string | undefined>(() =>
    integrationTypes.some((item) => item.id === initialIntegrationTypeId) ? initialIntegrationTypeId : undefined
  )
  const [selectedCredentialId, setSelectedCredentialId] = useState<string | undefined>()
  const [endpointKind, setEndpointKind] = useState('')
  const [configuration, setConfiguration] = useState<Record<string, string>>({})
  const [credentialInputs, setCredentialInputs] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | undefined>()
  const integration = integrationTypes.find((item) => item.id === selectedIntegrationId) ?? integrationTypes[0]
  const compatibleCredentials = credentialTypes.filter((item) => item.integration_type_revision_id === integration?.id)
  const credential = compatibleCredentials.find((item) => item.id === selectedCredentialId) ?? compatibleCredentials[0]
  const configurationFields = schemaFields(integration?.configuration_schema)
  const credentialFields = schemaFields(credential?.credential_schema)
  const connectionsQuery = pluginPlatformClient.useQuery(
    'get',
    '/plugin_connections',
    {
      params: {
        query: {
          project_id: stableProjectId ?? '00000000-0000-0000-0000-000000000000',
          integration_type_revision_id: integration?.id,
          enabled: true,
          include_total: true,
        },
      },
    },
    { enabled: Boolean(stableProjectId && integration) }
  )
  const { mutate: createConnection, isPending: isCreating } = pluginPlatformClient.useMutation(
    'post',
    '/plugin_connections'
  )
  const selectEndpointKind = (value: string) => {
    setEndpointKind(value)
    setConfiguration((current) => ({ ...current, endpointKind: value }))
    setError(undefined)
  }
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!stableProjectId || !integration || !credential || !name.trim() || !endpointKind) {
      setError('Select a project, integration type, endpoint kind, and a name before saving.')
      return
    }
    const missingFields = [
      ...missingRequiredFields(
        configurationFields.filter((field) => field.name !== 'endpointKind'),
        configuration
      ),
      ...missingRequiredFields(credentialFields, credentialInputs),
    ]
    if (missingFields.length > 0) {
      setError(`Complete required fields: ${missingFields.map((field) => field.title).join(', ')}.`)
      return
    }
    const existingConnection = connectionsQuery.data?.resources.find((connection) => connection.name === name.trim())
    if (existingConnection) {
      setError(
        `An integration named “${name.trim()}” already exists for this integration type in the selected project.`
      )
      return
    }
    const configuredValues = {
      ...populatedValues(configuration),
      endpointKind,
    }
    createConnection(
      {
        body: {
          name: name.trim(),
          project_id: stableProjectId,
          integration_type_revision_id: integration.id,
          credential_type_revision_id: credential.id,
          endpoint_kind: endpointKind,
          configuration: configuredValues,
          credential_inputs: populatedValues(credentialInputs),
        },
      },
      {
        onSuccess: () => {
          setName('')
          setCredentialInputs({})
          showSuccess({
            title: 'Integration saved',
            description: 'Syntara stored the credential securely. Its value is not shown again.',
          })
          detachPromise(connectionsQuery.refetch())
        },
        onError: handleMutationError({ title: 'Failed to save plugin integration' }),
      }
    )
  }
  return (
    <SynPanel>
      <Title headingLevel="h2">Plugin integrations</Title>
      <Content component="p">
        Create a reusable project integration from an installed plugin type. The plugin describes the fields; Syntara
        encrypts the credential and retains only a private reference.
      </Content>
      <Content component="p">
        The local PoC records and binds this integration to a workflow. It does not yet deliver credentials to the
        Execution Plane, so it cannot run a credential-bearing GitHub step.
      </Content>
      <Form onSubmit={submit}>
        <FormGroup label="Project" fieldId="plugin-integration-project" isRequired>
          {ProjectSelector}
        </FormGroup>
        <FormGroup label="Integration type" fieldId="plugin-integration-type" isRequired>
          <ValueSelect
            id="plugin-integration-type"
            value={integration.id}
            options={integrationTypes.map((item) => ({
              value: item.id,
              label: `${item.display_name} — Plugin: ${sourcePluginByIntegrationTypeId.get(item.id) ?? item.type_id}`,
            }))}
            placeholder="Select an integration type"
            onChange={(value) => {
              setSelectedIntegrationId(value)
              setSelectedCredentialId(undefined)
              setEndpointKind('')
              setConfiguration({})
              setCredentialInputs({})
              setError(undefined)
            }}
          />
        </FormGroup>
        <FormGroup label="Integration name" fieldId="plugin-integration-name" isRequired>
          <TextInput
            id="plugin-integration-name"
            value={name}
            autoComplete="off"
            onChange={(_event, value) => setName(value)}
          />
        </FormGroup>
        <FormGroup label="Endpoint kind" fieldId="plugin-integration-endpoint" isRequired>
          <ValueSelect
            id="plugin-integration-endpoint"
            value={endpointKind}
            options={integration.endpoint_kinds.map((value) => ({ value, label: humanize(value) }))}
            placeholder="Select an endpoint kind"
            onChange={selectEndpointKind}
          />
        </FormGroup>
        <SchemaFieldInputs
          fields={configurationFields.filter((field) => field.name !== 'endpointKind')}
          values={configuration}
          idPrefix="plugin-configuration"
          selectEnumValues
          onChange={(name, value) => setConfiguration((current) => ({ ...current, [name]: value }))}
        />
        {credential ? (
          <SchemaFieldInputs
            fields={credentialFields}
            values={credentialInputs}
            idPrefix="plugin-credential"
            secretFieldNames={credential.secret_field_names}
            onChange={(name, value) => setCredentialInputs((current) => ({ ...current, [name]: value }))}
          />
        ) : null}
        <FormHelperText>
          <HelperText>
            <HelperTextItem variant={error ? 'error' : 'default'}>
              {error ??
                (credential
                  ? `${credential.display_name} values are encrypted immediately after submission.`
                  : 'No compatible credential recipe is installed.')}
            </HelperTextItem>
          </HelperText>
        </FormHelperText>
        <Button variant="primary" type="submit" isLoading={isCreating} isDisabled={!stableProjectId || !credential}>
          Save integration
        </Button>
      </Form>
      <SavedPluginIntegrationNames connections={connectionsQuery.data?.resources ?? []} />
    </SynPanel>
  )
}
