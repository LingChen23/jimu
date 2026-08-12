import { describe, expect, it } from 'vitest'
import { mockDevices } from '@/mocks/devices'
import type { TaskSummary } from '@/types/task'
import {
  formatElapsedTime,
  getScoreView,
  isTaskBoundToDevice,
} from './realtime.view-model'

const task: TaskSummary = {
  taskId: 'TASK-1',
  participantId: 'USER-1',
  blockId: 'BLOCK-03',
  deviceId: 3,
  deviceIds: [3],
  taskType: '体验任务',
  startTime: '2026-08-06 10:00:00',
  endTime: null,
  durationSeconds: 75,
  totalScore: null,
  dataQuality: 'good',
  status: 'collecting',
}

describe('realtime requirement view model', () => {
  it('requires both device and block identifiers for task binding', () => {
    expect(isTaskBoundToDevice(task, mockDevices[2])).toBe(true)
    expect(isTaskBoundToDevice({ ...task, blockId: 'BLOCK-02' }, mockDevices[2])).toBe(false)
    expect(isTaskBoundToDevice({ ...task, deviceId: 2 }, mockDevices[2])).toBe(false)
  })

  it('returns an explicitly marked Mock score only while a task is collecting', () => {
    expect(getScoreView(task)).toEqual({
      title: '特征采集中',
      detail: '正式评分模型待接入',
      demoScore: 82,
    })
    expect(getScoreView(null)).toMatchObject({ title: '等待采集', demoScore: null })
  })

  it('formats elapsed time for the collection context', () => {
    expect(formatElapsedTime(75)).toBe('01:15')
    expect(formatElapsedTime(3_661)).toBe('1:01:01')
    expect(formatElapsedTime(null)).toBe('—')
  })
})
