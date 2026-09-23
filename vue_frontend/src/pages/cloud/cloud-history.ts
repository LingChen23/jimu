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
