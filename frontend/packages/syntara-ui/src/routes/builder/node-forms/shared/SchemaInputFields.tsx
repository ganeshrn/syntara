import {
  Checkbox,
  DatePicker,
  FormGroup,
  FormHelperText,
  HelperText,
  HelperTextItem,
  MenuToggle,
  type MenuToggleElement,
  SelectList,
  SelectOption,
  TextArea,
  TextInput,
} from '@patternfly/react-core'
import Form from '@rjsf/core'
import type { FieldTemplateProps, RJSFSchema, UiSchema, WidgetProps } from '@rjsf/utils'
import { customizeValidator } from '@rjsf/validator-ajv8'
import Ajv2020 from 'ajv/dist/2020'
import { useState, type Ref } from 'react'

import { SynSelect } from '../../../../components/SynSelect'
import { formatDateYMD, parseDateYMD } from '../../../../utils/dateUtils'

const validator = customizeValidator({
  AjvClass: Ajv2020,
  ajvOptionsOverrides: {
    coerceTypes: false,
    removeAdditional: false,
    useDefaults: false,
  },
})

function TextWidget(props: WidgetProps) {
  const numeric = props.schema.type === 'integer' || props.schema.type === 'number'
  const rawValue = props.value as unknown
  const value = typeof rawValue === 'string' || typeof rawValue === 'number' ? rawValue : ''
  let inputType: 'email' | 'number' | 'text' = 'text'
  if (numeric) inputType = 'number'
  else if (props.schema.format === 'email') inputType = 'email'
  return (
    <TextInput
      id={props.id}
      value={value}
      type={inputType}
      isDisabled={props.disabled}
      readOnlyVariant={props.readonly ? 'default' : undefined}
      placeholder={props.placeholder}
      validated={props.rawErrors?.length ? 'error' : 'default'}
      onChange={(_event, nextValue) => {
        let parsedValue: string | number | undefined = nextValue
        if (nextValue === '') parsedValue = undefined
        else if (numeric) parsedValue = Number(nextValue)
        props.onChange(parsedValue)
      }}
    />
  )
}

function TextareaWidget(props: WidgetProps) {
  const rawValue = props.value as unknown
  return (
    <TextArea
      id={props.id}
      value={typeof rawValue === 'string' ? rawValue : ''}
      isDisabled={props.disabled}
      readOnlyVariant={props.readonly ? 'default' : undefined}
      placeholder={props.placeholder}
      validated={props.rawErrors?.length ? 'error' : 'default'}
      resizeOrientation="vertical"
      onChange={(_event, value) => props.onChange(value)}
    />
  )
}

function PasswordWidget(props: WidgetProps) {
  const rawValue = props.value as unknown
  return (
    <TextInput
      id={props.id}
      value={typeof rawValue === 'string' ? rawValue : ''}
      type="password"
      isDisabled={props.disabled}
      readOnlyVariant={props.readonly ? 'default' : undefined}
      placeholder={props.placeholder}
      validated={props.rawErrors?.length ? 'error' : 'default'}
      onChange={(_event, value) => props.onChange(value)}
    />
  )
}

function DateWidget(props: WidgetProps) {
  const rawValue = props.value as unknown
  return (
    <DatePicker
      value={typeof rawValue === 'string' ? rawValue : ''}
      onChange={(_event, value) => props.onChange(value || undefined)}
      dateFormat={formatDateYMD}
      dateParse={parseDateYMD}
      aria-label={props.label}
      inputProps={{
        id: props.id,
        isDisabled: props.disabled,
        validated: props.rawErrors?.length ? 'error' : 'default',
      }}
      appendTo={() => document.body}
    />
  )
}

function CheckboxWidget(props: WidgetProps) {
  return (
    <Checkbox
      id={props.id}
      label={props.label}
      isChecked={Boolean(props.value)}
      isDisabled={props.disabled || props.readonly}
      onChange={(_event, checked) => props.onChange(checked)}
    />
  )
}

function SelectWidget(props: WidgetProps) {
  const [isOpen, setIsOpen] = useState(false)
  const options = props.options.enumOptions ?? []
  const selectedValue = props.value as unknown
  const selected = options.find((option) => (option.value as unknown) === selectedValue)
  return (
    <SynSelect
      id={props.id}
      isOpen={isOpen}
      selected={selectedValue}
      onOpenChange={setIsOpen}
      onSelect={(_event, value) => {
        props.onChange(value)
        setIsOpen(false)
      }}
      shouldFocusToggleOnSelect
      toggle={(toggleRef: Ref<MenuToggleElement>) => (
        <MenuToggle
          ref={toggleRef}
          isExpanded={isOpen}
          isDisabled={props.disabled || props.readonly}
          isFullWidth
          isPlaceholder={!selected}
          onClick={() => setIsOpen((current) => !current)}
        >
          {selected?.label ?? 'Select a value'}
        </MenuToggle>
      )}
    >
      <SelectList>
        {options.map((option) => (
          <SelectOption key={String(option.value as unknown)} value={option.value as unknown}>
            {option.label}
          </SelectOption>
        ))}
      </SelectList>
    </SynSelect>
  )
}

function FieldTemplate(props: FieldTemplateProps) {
  if (props.schema.type === 'object' || props.schema.type === 'array') return props.children
  const errors = props.rawErrors ?? []
  return (
    <FormGroup
      fieldId={props.id}
      label={props.displayLabel && props.schema.type !== 'boolean' ? props.label : undefined}
      isRequired={props.required}
    >
      {props.children}
      {errors.length > 0 ? (
        <FormHelperText>
          <HelperText>
            <HelperTextItem variant="error">{errors.join('; ')}</HelperTextItem>
          </HelperText>
        </FormHelperText>
      ) : null}
    </FormGroup>
  )
}

type SchemaInputFieldsProps = {
  schema: RJSFSchema
  uiSchema?: UiSchema
  value: Record<string, unknown>
  onChange: (value: Record<string, unknown>) => void
}

export function SchemaInputFields({ schema, uiSchema, value, onChange }: Readonly<SchemaInputFieldsProps>) {
  return (
    <Form
      schema={schema}
      uiSchema={uiSchema}
      validator={validator}
      widgets={{
        CheckboxWidget,
        DateWidget,
        PasswordWidget,
        SelectWidget,
        TextWidget,
        TextareaWidget,
        UpDownWidget: TextWidget,
      }}
      templates={{ FieldTemplate }}
      formData={value}
      idPrefix="external-action-input"
      tagName="div"
      noHtml5Validate
      showErrorList={false}
      onChange={(event) => {
        const formData = event.formData as unknown
        onChange(typeof formData === 'object' && formData !== null ? (formData as Record<string, unknown>) : {})
      }}
    />
  )
}
