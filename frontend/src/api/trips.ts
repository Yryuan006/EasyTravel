import { ApiError } from './health'
import { object, validId, validTrip, validNext, type TripView, type NextQuestionResponse } from './contracts'

const messages: Record<number, string> = {
  401: '本地凭证无效或未提供，请重新输入。', 403: '本地环境不允许访问，请检查后端开发配置。',
  404: '旅行不存在或当前身份无权读取。', 409: '请求与当前状态冲突，请核对后重试。',
  422: '输入有误，请检查日期、人数、金额和需求内容。', 503: '后端业务依赖不可用，请稍后重试。',
}
export interface RequestOptions { fetcher?: typeof fetch; timeoutMs?: number; signal?: AbortSignal }
export class TripApiError extends ApiError {
  constructor(kind: 'http' | 'network' | 'timeout' | 'contract', message: string, status?: number, public readonly code?: string) { super(kind, message, status) }
}
export async function request<T>(path: string, token: string, command: { key: string; body: string } | undefined, options: RequestOptions, method: 'POST' | 'PUT' | 'GET', validate: (v: unknown) => v is T, expectedStatus: number, expectedId?: string): Promise<T> {
  const controller = new AbortController()
  const abort = () => controller.abort()
  options.signal?.addEventListener('abort', abort, { once: true })
  if (options.signal?.aborted) abort()
  let timedOut = false
  const timer = setTimeout(() => { timedOut = true; controller.abort() }, options.timeoutMs ?? 12000)
  try {
    const response = await (options.fetcher ?? fetch)(path, { method, cache: 'no-store', credentials: 'omit', signal: controller.signal,
      headers: { Accept: 'application/json', Authorization: `Bearer ${token}`, ...(command ? { 'Content-Type': 'application/json', 'Idempotency-Key': command.key } : {}) },
      ...(command ? { body: command.body } : {}) })
    if (!response.ok) {
      let code: string | undefined
      try { const b: unknown = await response.json(); if (object(b) && object(b.error) && typeof b.error.code === 'string') code = b.error.code } catch { /* Never echo response diagnostics. */ }
      const message = response.status === 409 && code === 'command_in_progress' ? '原请求仍在处理中，请稍后使用原 key 和原内容重试。'
        : response.status === 409 && code === 'idempotency_key_reused' ? '幂等键已用于不同内容，不能自动重试。请核对原请求。'
        : messages[response.status] ?? `后端请求失败（HTTP ${response.status}），请稍后重试。`
      throw new TripApiError('http', message, response.status, code)
    }
    if (response.status !== expectedStatus) throw new TripApiError('contract', '后端返回了非预期成功状态，结果尚未确定，请勿重复新建。')
    let body: unknown
    try { body = await response.json() } catch { throw new TripApiError('contract', '后端响应不是有效 JSON，结果尚未确定。') }
    if (!validate(body)) throw new TripApiError('contract', '旅行响应不符合接口契约，结果尚未确定。')
    if (expectedId && object(body) && body.trip_id !== expectedId) throw new TripApiError('contract', '返回的旅行 ID 与请求不一致。')
    return body
  } catch (error) {
    if (timedOut) throw new TripApiError('timeout', '请求超时，结果尚未确定。写请求超时或取消不代表服务器事务已撤销。')
    if (error instanceof ApiError) throw error
    throw new TripApiError('network', '网络连接中断，结果尚未确定。写请求可能已经提交，请使用原 key 和原内容重试。')
  } finally { clearTimeout(timer); options.signal?.removeEventListener('abort', abort) }
}
export function createTrip(token: string, command: { key: string; body: string }, options: RequestOptions = {}) { return request('/api/v1/trips', token, command, options, 'POST', validTrip, 201) }
export function getTrip(token: string, id: string, options: RequestOptions = {}) {
  if (!validId(id)) return Promise.reject(new TripApiError('contract', '旅行地址中的 ID 格式无效。'))
  return request(`/api/v1/trips/${id}`, token, undefined, options, 'GET', validTrip, 200, id)
}

export function updateRequirements(token: string, id: string, command: { key: string; body: string }, options: RequestOptions = {}) {
  return request<TripView>(`/api/v1/trips/${id}/requirements`, token, command, options, 'PUT', validTrip, 200, id)
}
export function answerQuestion(token: string, id: string, questionId: string, command: { key: string; body: string }, options: RequestOptions = {}) {
  return request<TripView>(`/api/v1/trips/${id}/clarifications/${questionId}/answers`, token, command, options, 'POST', validTrip, 200, id)
}
export function getNextQuestion(token: string, id: string, options: RequestOptions = {}) {
  return request<NextQuestionResponse>(`/api/v1/trips/${id}/clarifications/next`, token, undefined, options, 'GET', validNext, 200)
}
