import schemas from './working-schema.json'
import type { WorkingDraft } from './contracts'
// The read-only legacy draft subset is copied verbatim from the actual OpenAPI.
// Domain consistency remains the backend's responsibility; no plan is inferred here.
type Schema = { $ref?: string; anyOf?: Schema[]; type?: string; const?: unknown; enum?: unknown[]; required?: string[]; properties?: Record<string, Schema>; additionalProperties?: boolean; items?: Schema; minItems?: number; maxItems?: number; minLength?: number; maxLength?: number; pattern?: string; format?: string; minimum?: number; maximum?: number }
function matches(v: unknown, s: Schema): boolean {
  if (s.$ref) return matches(v, (schemas as Record<string, Schema>)[s.$ref.split('/').at(-1)!]!)
  if (s.anyOf) return s.anyOf.some(x => matches(v, x))
  if ('const' in s && v !== s.const || s.enum && !s.enum.includes(v)) return false
  if (s.type === 'null') return v === null
  if (s.type === 'object') {
    if (typeof v !== 'object' || v === null || Array.isArray(v)) return false
    const o = v as Record<string, unknown>
    return (s.required ?? []).every(k => Object.hasOwn(o, k)) && Object.entries(o).every(([k, x]) => s.properties?.[k] ? matches(x, s.properties[k]) : s.additionalProperties !== false)
  }
  if (s.type === 'array') return Array.isArray(v) && v.length >= (s.minItems ?? 0) && v.length <= (s.maxItems ?? Infinity) && v.every(x => !s.items || matches(x, s.items))
  if (s.type === 'string') return typeof v === 'string' && v.length >= (s.minLength ?? 0) && v.length <= (s.maxLength ?? Infinity) && (!s.pattern || new RegExp(s.pattern).test(v)) && (!s.format || (s.format === 'date' ? /^\d{4}-\d{2}-\d{2}$/.test(v) && !Number.isNaN(Date.parse(v)) && new Date(v).toISOString().slice(0, 10) === v : /^\d{4}-.*T.*(?:Z|[+-]\d{2}:\d{2})$/.test(v) && !Number.isNaN(Date.parse(v))))
  if (s.type === 'integer' || s.type === 'number') return typeof v === 'number' && Number.isFinite(v) && (s.type !== 'integer' || Number.isSafeInteger(v)) && v >= (s.minimum ?? -Infinity) && v <= (s.maximum ?? Infinity)
  if (s.type === 'boolean') return typeof v === 'boolean'
  return true
}
export function validWorking(v: unknown): v is WorkingDraft { return matches(v, schemas.WorkingDraft as Schema) }
