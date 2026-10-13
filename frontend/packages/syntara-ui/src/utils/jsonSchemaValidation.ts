/**
 * Check whether a value matches a JSON Schema primitive type.
 *
 * This intentionally supports the small, shared authoring preflight used by
 * the workflow-run dialog and installed-action form. The backend remains the
 * authority for complete schema validation at publication and execution.
 */
function matchesJsonSchemaType(value: unknown, expectedType: string): boolean {
  switch (expectedType) {
    case 'string':
      return typeof value === 'string'
    case 'number':
      return typeof value === 'number'
    case 'integer':
      return typeof value === 'number' && Number.isInteger(value)
    case 'boolean':
      return typeof value === 'boolean'
    case 'array':
      return Array.isArray(value)
    case 'object':
      return typeof value === 'object' && value !== null && !Array.isArray(value)
    default:
      return true
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/**
 * Perform lightweight JSON Schema preflight validation.
 *
 * It validates top-level required properties and primitive types without
 * interpreting references, executable expressions, or remote schemas.
 */
export function validateJsonSchemaObject(data: Record<string, unknown>, schema: Record<string, unknown>): string[] {
  if (schema.type !== 'object') return []

  const errors: string[] = []
  const required = Array.isArray(schema.required)
    ? schema.required.filter((field): field is string => typeof field === 'string')
    : []
  for (const field of required) {
    if (!(field in data) || data[field] === undefined || data[field] === null) {
      errors.push(`Missing required field: "${field}"`)
    }
  }

  const properties = schema.properties
  if (!isRecord(properties)) return errors
  for (const [key, rawProperty] of Object.entries(properties)) {
    if (!(key in data) || data[key] === null || data[key] === undefined) continue
    if (!isRecord(rawProperty)) continue
    const expectedType = rawProperty.type
    if (typeof expectedType === 'string' && !matchesJsonSchemaType(data[key], expectedType)) {
      errors.push(`Field "${key}" should be ${expectedType}, got ${typeof data[key]}`)
    }
  }

  return errors
}
