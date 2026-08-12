import type { TaskStatus, TaskSummary } from '@/types/task'

export interface TaskHistoryFilters {
  taskId: string
  participantId: string
  status: 'all' | TaskStatus
  date: string
}

export function filterTasks(tasks: TaskSummary[], filters: TaskHistoryFilters): TaskSummary[] {
  return tasks.filter((task) => (
    task.taskId.includes(filters.taskId.trim())
    && task.participantId.includes(filters.participantId.trim())
    && (filters.status === 'all' || task.status === filters.status)
    && (!filters.date || task.startTime.startsWith(filters.date))
  ))
}
