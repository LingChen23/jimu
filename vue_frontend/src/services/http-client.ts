export const DEFAULT_REQUEST_TIMEOUT_MS = 20_000

export async function fetchWithTimeout(
  input: RequestInfo | URL,
  init: RequestInit = {},
  timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
): Promise<Response> {
  const controller = new AbortController()
  const timeout = globalThis.setTimeout(() => controller.abort(), timeoutMs)

  try {
    return await fetch(input, { ...init, signal: controller.signal })
  } catch (error) {
    if (controller.signal.aborted) {
      if (['127.0.0.1', 'localhost'].includes(location.hostname)) {
        void fetch('/api/diagnostics/event', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code: 'page_request_timeout', operation: 'browser_page' }),
          keepalive: true,
        }).catch(() => {})
      }
      throw new Error(`请求超时（${Math.ceil(timeoutMs / 1_000)} 秒），请检查后端服务。`)
    }
    throw error
  } finally {
    globalThis.clearTimeout(timeout)
  }
}
