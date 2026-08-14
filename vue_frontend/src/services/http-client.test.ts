import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchWithTimeout } from './http-client'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('fetchWithTimeout', () => {
  it('aborts a request that never settles', async () => {
    vi.stubGlobal('fetch', vi.fn((_input: RequestInfo | URL, init?: RequestInit) => (
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => reject(new Error('aborted')), { once: true })
      })
    )))

    await expect(fetchWithTimeout('/api/live', {}, 5)).rejects.toThrow('请求超时')
  })

  it('returns a response before the deadline', async () => {
    const response = new Response('{}', { status: 200 })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response))

    await expect(fetchWithTimeout('/api/status', {}, 50)).resolves.toBe(response)
  })
})
