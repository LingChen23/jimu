import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import type { TaskSummary } from '@/types/task'
import { useTaskStore } from './task.store'

const service = vi.hoisted(() => ({
  deleteTask: vi.fn(), listCloudTasks: vi.fn(), getTaskResult: vi.fn(),
}))
vi.mock('@/services/data.service', () => ({ motionDataService: service }))
const task = (overrides: Partial<TaskSummary> = {}) => ({
  taskId: 'T-TEST', status: 'completed', localAvailable: true, cloudArchived: true,
  startTime: '2026-10-26', ...overrides,
}) as TaskSummary

describe('task copy deletion', () => {
  beforeEach(() => { setActivePinia(createPinia()); vi.resetAllMocks() })

  it('retains cloud record after local deletion, then removes it after cloud deletion', async () => {
    const store = useTaskStore()
    store.tasks = [task()]
    await store.deleteTask('T-TEST', 'local')
    expect(store.tasks[0]).toMatchObject({ localAvailable: false, cloudArchived: true })
    await store.deleteTask('T-TEST', 'cloud')
    expect(store.tasks).toEqual([])
    expect(service.deleteTask.mock.calls.map(([request]) => request.scope)).toEqual(['local', 'cloud'])
  })

  it('retains local data after cloud deletion and preserves records on failure', async () => {
    const store = useTaskStore()
    store.tasks = [task()]
    service.deleteTask.mockRejectedValueOnce(new Error('cloud_connection_failed'))
    await expect(store.deleteTask('T-TEST', 'cloud')).rejects.toThrow('cloud_connection_failed')
    expect(store.tasks[0]?.cloudArchived).toBe(true)
    await store.deleteTask('T-TEST', 'cloud')
    expect(store.tasks[0]).toMatchObject({ localAvailable: true, cloudArchived: false })
  })

  it('does not resurrect a deleted cloud copy from an in-flight list response', async () => {
    const store = useTaskStore()
    store.tasks = [task({ localAvailable: false })]
    let resolve!: (value: unknown) => void
    service.listCloudTasks.mockReturnValue(new Promise(done => { resolve = done }))
    const loading = store.mergeCloudTasks()
    await store.deleteTask('T-TEST', 'cloud')
    resolve({ tasks: [task({ localAvailable: false })], total: 1 })
    await loading
    expect(store.tasks).toEqual([])
  })
})
