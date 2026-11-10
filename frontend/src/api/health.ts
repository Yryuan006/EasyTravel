/** Normalized M0-01 OpenAPI HealthResponse. No readiness inference is allowed. */
export interface HealthResponse {
  status: 'alive'
  scope: 'http_application_liveness'
  dependencies: {
    database: 'not_checked'
    model: 'not_checked'
    travel_data: 'not_checked'
  }
}

export type ApiErrorKind = 'http' | 'network' | 'timeout' | 'contract'

export class ApiError extends Error {
  constructor(
    public readonly kind: ApiErrorKind,
    message: string,
    public readonly status?: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function parseHealth(value: unknown): HealthResponse {
  const invalid = () => new ApiError('contract', '服务响应不符合健康接口契约，请稍后重试。')
  if (!isObject(value)) throw invalid()
  // OpenAPI fields are optional with explicit defaults; reject conflicting values.
  if (value.status !== undefined && value.status !== 'alive') throw invalid()
  if (value.scope !== undefined && value.scope !== 'http_application_liveness') throw invalid()
  const deps = value.dependencies === undefined ? {} : value.dependencies
  if (!isObject(deps)) throw invalid()
  for (const key of ['database', 'model', 'travel_data']) {
    if (deps[key] !== undefined && deps[key] !== 'not_checked') throw invalid()
  }
  return {
    status: 'alive',
    scope: 'http_application_liveness',
    dependencies: { database: 'not_checked', model: 'not_checked', travel_data: 'not_checked' },
  }
}

/** Same-origin URL: dev uses Vite proxy; production requires reverse proxy /api. */
export async function getHealth(options: {
  fetcher?: typeof fetch
  signal?: AbortSignal
  timeoutMs?: number
} = {}): Promise<HealthResponse> {
  const controller = new AbortController()
  const abort = () => controller.abort()
  options.signal?.addEventListener('abort', abort, { once: true })
  if (options.signal?.aborted) abort()
  let timedOut = false
  const timer = setTimeout(() => { timedOut = true; controller.abort() }, options.timeoutMs ?? 5000)
  try {
    const response = await (options.fetcher ?? fetch)('/api/v1/health', {
      signal: controller.signal,
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    })
    if (!response.ok) {
      throw new ApiError('http', `后端暂时无法响应（HTTP ${response.status}），请稍后重试。`, response.status)
    }
    let body: unknown
    try { body = await response.json() }
    catch { throw new ApiError('contract', '服务返回了无法解析的响应，请稍后重试。') }
    return parseHealth(body)
  } catch (error) {
    if (timedOut) throw new ApiError('timeout', '连接后端超时，请稍后重试。')
    if (error instanceof ApiError) throw error
    throw new ApiError('network', '无法连接后端服务，请确认服务已启动后重试。')
  } finally {
    clearTimeout(timer)
    options.signal?.removeEventListener('abort', abort)
  }
}
