import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import App from '../src/App.vue'
import HealthStatus from '../src/components/HealthStatus.vue'
import { parseHealth } from '../src/api/health'

afterEach(() => { vi.unstubAllGlobals() })

it('shows accessible loading and disables repeated clicks', () => {
  const wrapper = mount(HealthStatus, { props: { state: { status: 'loading' } } })
  expect(wrapper.get('[role="status"]').text()).toContain('正在连接后端')
  expect(wrapper.get('button').attributes('disabled')).toBeDefined()
  expect(wrapper.get('section').attributes('aria-busy')).toBe('true')
  wrapper.unmount()
})

it('limits success to HTTP liveness and emits retry', async () => {
  const wrapper = mount(HealthStatus, { props: { state: {
    status: 'success', data: parseHealth({}), checkedAt: new Date('2026-11-10T12:00:00Z'),
  } } })
  expect(wrapper.get('[role="status"]').text()).toContain('HTTP 应用存活')
  expect(wrapper.findAll('dd').map(node => node.text())).toEqual(['未检查', '未检查', '未检查'])
  expect(wrapper.text()).toContain('是否就绪，尚未验证')
  await wrapper.get('.health-panel button').trigger('click')
  expect(wrapper.emitted('refresh')).toHaveLength(1)
  wrapper.unmount()
})

it('shows failure without retaining success', () => {
  const wrapper = mount(HealthStatus, { props: { state: { status: 'error', message: '无法连接后端服务' } } })
  expect(wrapper.get('[role="status"]').text()).toContain('无法连接后端服务')
  expect(wrapper.text()).not.toContain('健康检查已通过')
  expect(wrapper.findAll('dd').every(node => node.text() === '未检查')).toBe(true)
  wrapper.unmount()
})

it('renders the app, recovers from failure, and clears old success on the next check', async () => {
  const fetcher = vi.fn<typeof fetch>()
    .mockRejectedValueOnce(new TypeError('offline'))
    .mockResolvedValueOnce(new Response('{}'))
    .mockRejectedValueOnce(new TypeError('offline'))
  vi.stubGlobal('fetch', fetcher)
  const wrapper = mount(App)
  expect(wrapper.text()).toContain('EasyTravel')
  expect(wrapper.find('main').exists()).toBe(true)
  expect(wrapper.text()).toContain('正在连接后端')
  await flushPromises()
  expect(wrapper.text()).toContain('健康检查失败')
  await wrapper.get('.health-panel button').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('HTTP 应用存活')
  await wrapper.get('.health-panel button').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('健康检查失败')
  expect(wrapper.text()).not.toContain('健康检查已通过')
  wrapper.unmount()
})

it('aborts an in-flight request on unmount', () => {
  let signal: AbortSignal | null | undefined
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation((_url, init) => {
    signal = init?.signal
    return new Promise((_resolve, reject) => signal?.addEventListener('abort', () => reject(new Error('aborted'))))
  }))
  const wrapper = mount(App)
  wrapper.unmount()
  expect(signal?.aborted).toBe(true)
})
