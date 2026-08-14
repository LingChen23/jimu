export const DEFAULT_REQUEST_TIMEOUT_MS = 5_000

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
      throw new Error(`请求超时（${Math.ceil(timeoutMs / 1_000)} 秒），请检查后端服务。`)
    }
    throw error
  } finally {
    globalThis.clearTimeout(timeout)
  }
}
