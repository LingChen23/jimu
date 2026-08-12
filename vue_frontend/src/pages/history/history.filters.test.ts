import { describe, expect, it } from 'vitest'
import { mockTasks } from '@/mocks/tasks'
import { filterTasks } from './history.filters'

describe('history filters', () => {
  it('filters tasks by participant, status and date', () => {
    const result = filterTasks(mockTasks, {
      taskId: '',
      participantId: 'U001',
      status: 'completed',
      date: '2026-07-29',
    })
    expect(result.map((task) => task.taskId)).toEqual(['T20260729-001'])
  })
})
