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
      if (typeof location !== 'undefined' && ['127.0.0.1', 'localhost'].includes(location.hostname)) {
        let taskId = ''
        try {
          const url = new URL(input instanceof Request ? input.url : String(input), location.href)
          taskId = url.searchParams.get('task') ?? url.searchParams.get('task_id') ?? ''
        } catch { /* Keep timeout reporting independent of URL parsing. */ }
        if (!taskId) taskId = location.pathname.match(/^\/results\/([A-Za-z0-9_-]{1,96})$/)?.[1] ?? ''
        void fetch('/api/diagnostics/event', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code: 'page_request_timeout', operation: 'browser_page', task_id: taskId }),
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
