import { fetchWithTimeout } from '@/services/http-client'

export interface CloudHistoryTask {
  taskId: string
  participantId: string
  participantName: string
  startTime: string
  endTime: string
  taskStatus: string
  movementCount: number
  totalScore: number | null
  scoreModelVersion: string | null
  zipSize: number
  uploadedAt: string
}

export interface CloudHistoryPageData {
  items: CloudHistoryTask[]
  page: number
  pageSize: number
  total: number
}

export interface CloudHistoryFilters {
  taskId: string
  participantId: string
  participantName: string
  date: string
}

export function historyQuery(filters: CloudHistoryFilters, page: number, pageSize = 20): string {
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  for (const [key, value] of Object.entries({
    task_id: filters.taskId,
    participant_id: filters.participantId,
    participant_name: filters.participantName,
    date: filters.date,
  })) {
    if (value.trim()) query.set(key, value.trim())
  }
  return query.toString()
}

export function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}

export async function deleteArchivedTasks(taskIds: string[], onDeleted: (taskId: string) => void) {
  const deleted: string[] = []
  const failures: Array<{ taskId: string; message: string }> = []
  for (const taskId of new Set(taskIds)) {
    try {
      const response = await fetchWithTimeout(`/api/history/tasks/${encodeURIComponent(taskId)}`, {
        method: 'DELETE',
      }, 130_000)
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) {
        const code = String(payload?.error?.code ?? `HTTP ${response.status}`)
        throw new Error(({
          archive_busy: '云端正在归档，请稍后重试',
          oss_delete_failed: '文件删除失败，请重试',
        } as Record<string, string>)[code] ?? code)
      }
      if (payload.deleted !== true || payload.task_id !== taskId) throw new Error('未确认删除成功，请重试')
    } catch (error) {
      failures.push({ taskId, message: error instanceof Error ? error.message : String(error) })
      continue
    }
    deleted.push(taskId)
    onDeleted(taskId)
  }
  return { deleted, failures }
}
