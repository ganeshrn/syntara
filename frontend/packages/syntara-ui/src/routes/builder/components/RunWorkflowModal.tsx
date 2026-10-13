import { Button, HelperText, HelperTextItem, Modal, ModalBody, ModalFooter, ModalHeader } from '@patternfly/react-core'
import { useCallback, useMemo, useState } from 'react'

import { executionsClient } from '../../../client'
import { useBlurOnOpen } from '../../../hooks/useBlurOnOpen'
import { useAlerts } from '../../../providers/alerts'
import { schemaToTemplateJson } from '../../../utils/jsonSchemaTemplate'
import { validateJsonSchemaObject } from '../../../utils/jsonSchemaValidation'

import { ExpandableCodeEditor } from './ExpandableCodeEditor'
import { JsonEditorControls } from './JsonEditorToolbar'

type RunWorkflowModalProps = Readonly<{
  isOpen: boolean
  onClose: () => void
  onConfirm: (inputData: Record<string, unknown>, triggerNodeId?: string) => void
  workflowName: string
  triggerName: string
  triggerNodeId?: string
  inputSchema?: Record<string, unknown>
  workflowId?: string | null
}>

export function RunWorkflowModal({
  isOpen,
  onClose,
  onConfirm,
  workflowName,
  triggerName,
  triggerNodeId,
  inputSchema,
  workflowId,
}: RunWorkflowModalProps) {
  useBlurOnOpen(isOpen)
  const { showError } = useAlerts()

  const latestExecQuery = executionsClient.useQuery(
    'get',
    '/executions',
    {
      params: {
        query: {
          workflow_id: workflowId ?? '',
          status: 'completed',
          sort: '-created_at',
          limit: 1,
        },
      },
    },
    { enabled: isOpen && !!workflowId }
  )

  const latestExecutionId = useMemo(() => {
    const resources = latestExecQuery.data?.resources
    if (resources && resources.length > 0) {
      return resources[0].id
    }
    return null
  }, [latestExecQuery.data])

  const activitiesQuery = executionsClient.useQuery(
    'get',
    '/executions/{execution_id}/activities',
    {
      params: { path: { execution_id: latestExecutionId ?? '' } },
    },
    { enabled: !!latestExecutionId }
  )

  const previousTriggerData = useMemo(() => {
    if (!activitiesQuery.data || !triggerNodeId) return null
    const activities = activitiesQuery.data.resources ?? []
    const triggerActivity = activities.find((a) => a.activity_name === triggerNodeId)
    if (triggerActivity?.output_data && typeof triggerActivity.output_data === 'object') {
      return triggerActivity.output_data
    }
    return null
  }, [activitiesQuery.data, triggerNodeId])

  const schemaTemplate = useMemo(() => {
    if (!inputSchema) return '{}'
    return schemaToTemplateJson(inputSchema)
  }, [inputSchema])

  const defaultCode = useMemo(
    () => (previousTriggerData ? JSON.stringify(previousTriggerData, null, 2) : schemaTemplate),
    [previousTriggerData, schemaTemplate]
  )

  const [userCode, setUserCode] = useState<string | null>(null)
  const code = userCode ?? defaultCode
  const setCode = useCallback((value: string) => setUserCode(value), [])

  const isPrePopulated = previousTriggerData !== null && userCode === null

  const handleConfirm = useCallback(() => {
    let parsed: Record<string, unknown>
    try {
      const raw: unknown = JSON.parse(code.trim() || '{}')
      if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
        showError({
          title: 'Invalid input',
          description: 'Input data must be a JSON object, not an array or primitive value.',
        })
        return
      }
      parsed = raw as Record<string, unknown>
    } catch {
      showError({
        title: 'Invalid JSON',
        description: 'The data must be valid JSON.',
      })
      return
    }

    if (inputSchema) {
      const errors = validateJsonSchemaObject(parsed, inputSchema)
      if (errors.length > 0) {
        showError({
          title: 'Validation failed',
          description: errors.join('; '),
        })
        return
      }
    }

    onConfirm(parsed, triggerNodeId)
  }, [code, onConfirm, showError, inputSchema, triggerNodeId])

  return (
    <Modal isOpen={isOpen} onClose={onClose} variant="medium" aria-label="Run workflow modal">
      <ModalHeader
        title={`Set mock output data for ${triggerName}`}
        description="This data will be accessible for downstream nodes."
      />
      <ModalBody>
        <ExpandableCodeEditor
          code={code}
          onCodeChange={setCode}
          language="json"
          height="300px"
          modalTitle="Edit data"
          ariaLabel="Workflow data editor"
          additionalControls={
            <JsonEditorControls
              code={code}
              onCodeChange={setCode}
              defaultCode={defaultCode}
              downloadFilename={`${workflowName}-data.json`}
            />
          }
        />
        {isPrePopulated && (
          <HelperText>
            <HelperTextItem variant="indeterminate">Pre-populated from the last successful run</HelperTextItem>
          </HelperText>
        )}
      </ModalBody>
      <ModalFooter>
        <Button variant="primary" onClick={handleConfirm}>
          Run
        </Button>
        <Button variant="link" onClick={onClose}>
          Cancel
        </Button>
      </ModalFooter>
    </Modal>
  )
}
