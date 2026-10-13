import {
  Alert,
  Button,
  Form,
  FormGroup,
  HelperText,
  HelperTextItem,
  Modal,
  ModalBody,
  ModalFooter,
  ModalHeader,
  Stack,
  StackItem,
  TextInput,
} from '@patternfly/react-core'
import type { PluginPlatformAPI } from '@syntara/contracts'
import { useState } from 'react'

type PluginSourceCreate = PluginPlatformAPI.components['schemas']['PluginSourceCreate']

const localPocDefaults: PluginSourceCreate = {
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
}

type PluginSourceDialogProps = Readonly<{
  isOpen: boolean
  isCreating: boolean
  onClose: () => void
  onCreate: (source: PluginSourceCreate) => void
}>

/**
 * Deliberately narrow v1 source form for the local development catalog.
 * Production credential, trust, CA, and mirror-policy management remains a
 * server-managed administration concern rather than a browser secret flow.
 */
export function PluginSourceDialog({ isOpen, isCreating, onClose, onCreate }: PluginSourceDialogProps) {
  const [source, setSource] = useState<PluginSourceCreate>(localPocDefaults)

  const setValue = <K extends keyof PluginSourceCreate>(key: K, value: PluginSourceCreate[K]) => {
    setSource((current) => ({ ...current, [key]: value }))
  }

  const canSubmit = Boolean(
    source.source_id.trim() &&
    source.display_name.trim() &&
    source.registry_origin.trim() &&
    source.index_repository.trim() &&
    source.channel.trim() &&
    source.repository_scopes.length > 0
  )

  return (
    <Modal isOpen={isOpen} onClose={onClose} variant="medium" aria-labelledby="add-plugin-source-title">
      <ModalHeader title="Add local plugin catalog" titleIconVariant="info" labelId="add-plugin-source-title" />
      <ModalBody>
        <Stack hasGutter>
          <StackItem>
            <Alert isInline variant="info" title="Local development only">
              This form records non-secret catalog location and policy references. It does not put a registry password,
              signing key, or token in the browser.
            </Alert>
          </StackItem>
          <StackItem>
            <Form>
              <FormGroup label="Source ID" isRequired fieldId="plugin-source-id">
                <TextInput
                  id="plugin-source-id"
                  value={source.source_id}
                  onChange={(_event, value) => setValue('source_id', value)}
                  isRequired
                />
              </FormGroup>
              <FormGroup label="Display name" isRequired fieldId="plugin-source-display-name">
                <TextInput
                  id="plugin-source-display-name"
                  value={source.display_name}
                  onChange={(_event, value) => setValue('display_name', value)}
                  isRequired
                />
              </FormGroup>
              <FormGroup label="Registry address" isRequired fieldId="plugin-source-registry-origin">
                <TextInput
                  id="plugin-source-registry-origin"
                  value={source.registry_origin}
                  onChange={(_event, value) => setValue('registry_origin', value)}
                  isRequired
                />
              </FormGroup>
              <FormGroup label="Catalog index repository" isRequired fieldId="plugin-source-index-repository">
                <TextInput
                  id="plugin-source-index-repository"
                  value={source.index_repository}
                  onChange={(_event, value) => setValue('index_repository', value)}
                  isRequired
                />
                <HelperText>
                  <HelperTextItem>The catalog index tells Syntara which plugin artifacts are visible.</HelperTextItem>
                </HelperText>
              </FormGroup>
              <FormGroup label="Catalog channel" isRequired fieldId="plugin-source-channel">
                <TextInput
                  id="plugin-source-channel"
                  value={source.channel}
                  onChange={(_event, value) => setValue('channel', value)}
                  isRequired
                />
              </FormGroup>
              <FormGroup label="Plugin repositories" isRequired fieldId="plugin-source-repository-scopes">
                <TextInput
                  id="plugin-source-repository-scopes"
                  value={source.repository_scopes.join(', ')}
                  onChange={(_event, value) =>
                    setValue(
                      'repository_scopes',
                      value
                        .split(',')
                        .map((scope) => scope.trim())
                        .filter(Boolean)
                    )
                  }
                  isRequired
                />
                <HelperText>
                  <HelperTextItem>Separate multiple approved repository prefixes with commas.</HelperTextItem>
                </HelperText>
              </FormGroup>
            </Form>
          </StackItem>
        </Stack>
      </ModalBody>
      <ModalFooter>
        <Button
          variant="primary"
          onClick={() => onCreate(source)}
          isDisabled={!canSubmit || isCreating}
          isLoading={isCreating}
        >
          Add catalog
        </Button>
        <Button variant="link" onClick={onClose} isDisabled={isCreating}>
          Cancel
        </Button>
      </ModalFooter>
    </Modal>
  )
}
