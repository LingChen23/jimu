import {
  getMockTaskResult as buildMockTaskResult,
  mockCurrentTask,
  mockTasks,
} from '@/mocks'
import type { TaskResult, TaskSummary } from '@/types/task'
import type { DeviceRequest, TaskResultRequest } from '../motion-data.service'
import {
  cloneMock,
  requireMockDevice,
  type MockServiceState,
} from './mock-service-state'

export function getMockCurrentTask(
  state: MockServiceState,
  request: DeviceRequest,
): TaskSummary | null {
  requireMockDevice(state, request.deviceId)
  return request.deviceId === mockCurrentTask.deviceId ? cloneMock(mockCurrentTask) : null
}

export function listMockTasks(): TaskSummary[] {
  return cloneMock(mockTasks)
}

export function getMockTaskResult(request: TaskResultRequest): TaskResult {
  return cloneMock(buildMockTaskResult(request.taskId))
}
