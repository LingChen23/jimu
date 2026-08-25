import { describe, expect, it } from 'vitest'
import { mockTasks } from '@/mocks/tasks'
import { filterTasks, taskDate } from './history.filters'

describe('history filters', () => {
  it('filters tasks by participant and date', () => {
    const result = filterTasks(mockTasks, {
      taskId: '',
      participantId: 'U001',
      participantName: '',
      date: '2026-07-29',
    })
    expect(result.map((task) => task.taskId)).toEqual(['T20260729-001'])
  })

  it('filters tasks by participant name without case sensitivity', () => {
    const tasks = mockTasks.map((task, index) => ({
      ...task,
      participantSnapshot: index === 1
        ? {
            participantId: task.participantId,
            name: 'Alice Zhang',
            gender: '',
            age: null,
            birthDate: '',
            educationLevel: '',
            dominantHand: '',
            phone: '',
            visionStatus: '',
            hearingStatus: '',
            upperLimbStatus: '',
            cognitiveStatus: '',
            profileNote: '',
          }
        : null,
    }))
    const result = filterTasks(tasks, {
      taskId: '',
      participantId: '',
      participantName: 'alice',
      date: '',
    })
    expect(result.map((task) => task.taskId)).toEqual(['T20260729-002'])
  })

  it('extracts a searchable local date from supported task timestamps', () => {
    expect(taskDate('2026-07-29 14:20:00')).toBe('2026-07-29')
    expect(taskDate('2026-07-29T14:20:00+08:00')).toBe('2026-07-29')
    expect(taskDate('not-a-date')).toBe('')
  })
})
