import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, getHealth, parseHealth } from '../src/api/health'
import contract from '../../Docs/M0-01_openapi.json'

const healthy = {
  status: 'alive', scope: 'http_application_liveness',
  dependencies: { database: 'not_checked', model: 'not_checked', travel_data: 'not_checked' },
}
const fetchJson = (data: unknown, status = 200) => vi.fn<typeof fetch>().mockResolvedValue(
  new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } }),
)

afterEach(() => { vi.useRealTimers() })

describe('health API boundary', () => {
  it('matches the archived OpenAPI literals and optional defaults', () => {
    const schemas = contract.components.schemas
    expect(healthy.status).toBe(schemas.HealthResponse.properties.status.const)
    expect(healthy.scope).toBe(schemas.HealthResponse.properties.scope.const)
    expect(healthy.dependencies).toEqual(schemas.HealthResponse.properties.dependencies.default)
    expect(parseHealth({})).toEqual(healthy)
    expect(parseHealth({ dependencies: {} })).toEqual(healthy)
  })
  it('requests the existing same-origin endpoint and returns typed liveness', async () => {
    const fetcher = fetchJson(healthy)
    expect(await getHealth({ fetcher })).toEqual(healthy)
    expect(fetcher).toHaveBeenCalledWith('/api/v1/health', expect.objectContaining({ cache: 'no-store' }))
  })
  it.each([404, 409, 422, 500, 502, 503])('rejects HTTP %s even with a healthy-looking body', async status => {
    await expect(getHealth({ fetcher: fetchJson(healthy, status) })).rejects.toMatchObject({ kind: 'http', status })
  })
  it('reports unreachable backend without exposing raw diagnostic secrets', async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new Error('synthetic-private-diagnostic'))
    await expect(getHealth({ fetcher })).rejects.toMatchObject({ kind: 'network' })
    await expect(getHealth({ fetcher })).rejects.not.toThrow('synthetic-private-diagnostic')
  })
  it.each([null, [], 'alive', { status: 'ready' }, { scope: 'readiness' }, { dependencies: null },
    { dependencies: { database: 'ready' } }, { dependencies: { model: 'ready' } },
    { dependencies: { travel_data: 'ready' } }].map(body => [body]))('rejects malformed or readiness payload %j', async body => {
    await expect(getHealth({ fetcher: fetchJson(body) })).rejects.toMatchObject({ kind: 'contract' })
  })
  it('rejects non-JSON success responses', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response('<html>error</html>'))
    await expect(getHealth({ fetcher })).rejects.toBeInstanceOf(ApiError)
    await expect(getHealth({ fetcher })).rejects.toMatchObject({ kind: 'contract' })
  })
  it('times out a stalled request', async () => {
    vi.useFakeTimers()
    const fetcher = vi.fn<typeof fetch>().mockImplementation((_url, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    }))
    const assertion = expect(getHealth({ fetcher, timeoutMs: 20 })).rejects.toMatchObject({ kind: 'timeout' })
    await vi.advanceTimersByTimeAsync(20)
    await assertion
  })
  it('propagates caller cancellation to fetch and releases the timer', async () => {
    const controller = new AbortController()
    const fetcher = vi.fn<typeof fetch>().mockImplementation((_url, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    }))
    const assertion = expect(getHealth({ fetcher, signal: controller.signal })).rejects.toMatchObject({ kind: 'network' })
    controller.abort()
    await assertion
    expect(fetcher.mock.calls[0]?.[1]?.signal?.aborted).toBe(true)
  })
})
