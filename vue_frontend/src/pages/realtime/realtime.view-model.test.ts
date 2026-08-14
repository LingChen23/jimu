import { describe, expect, it } from 'vitest'
import type { DeviceSnapshot } from '@/types/device'
import type { TaskSummary } from '@/types/task'
import {
  isTaskBoundToDevice,
} from './realtime.view-model'

const task: TaskSummary = {
  taskId: 'TASK-1',
  participantId: 'USER-1',
  blockId: 'BLOCK-03',
  deviceId: 3,
  deviceIds: [1, 2, 3],
  blockBindings: [
    { deviceId: 1, blockId: 'A' },
    { deviceId: 2, blockId: 'B' },
    { deviceId: 3, blockId: 'BLOCK-03' },
  ],
  note: '体验任务',
  startTime: '2026-08-06 10:00:00',
  endTime: null,
  durationSeconds: 75,
  totalScore: null,
  dataQuality: 'good',
  status: 'collecting',
}

const boundDevice = {
  blockId: 'BLOCK-03', deviceId: 3,
} as DeviceSnapshot

describe('realtime requirement view model', () => {
  it('requires both device and block identifiers for task binding', () => {
    expect(isTaskBoundToDevice(task, boundDevice)).toBe(true)
    expect(isTaskBoundToDevice({ ...task, blockBindings: [{ deviceId: 3, blockId: 'BLOCK-02' }] }, boundDevice)).toBe(false)
    expect(isTaskBoundToDevice({ ...task, deviceIds: [1, 2] }, boundDevice)).toBe(false)
  })
})
