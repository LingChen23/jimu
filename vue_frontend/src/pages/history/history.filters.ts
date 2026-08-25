import type { TaskSummary } from '@/types/task'

export interface TaskHistoryFilters {
  taskId: string
  participantId: string
  participantName: string
  date: string
}

export function taskDate(startTime: string): string {
  const parsed = new Date(startTime)
  if (!Number.isNaN(parsed.getTime())) {
    const year = parsed.getFullYear()
    const month = String(parsed.getMonth() + 1).padStart(2, '0')
    const day = String(parsed.getDate()).padStart(2, '0')
    return `${year}-${month}-${day}`
  }
  const fallback = /^(\d{4}-\d{2}-\d{2})(?:[T\s]|$)/.exec(startTime.trim())
  return fallback?.[1] ?? ''
}

export function filterTasks(tasks: TaskSummary[], filters: TaskHistoryFilters): TaskSummary[] {
  const nameKeyword = filters.participantName.trim().toLocaleLowerCase()
  return tasks.filter((task) => (
    task.taskId.includes(filters.taskId.trim())
    && task.participantId.includes(filters.participantId.trim())
    && (task.participantSnapshot?.name ?? '').toLocaleLowerCase().includes(nameKeyword)
    && (!filters.date || taskDate(task.startTime) === filters.date)
  ))
}
