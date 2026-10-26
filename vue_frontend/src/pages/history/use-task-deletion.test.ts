import { describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import type { CloudUploadTaskStatus, TaskSummary } from '@/types/task'
import { useTaskDeletion } from './use-task-deletion'

describe('batch task deletion', () => {
  it('continues after a failure and reports only successful IDs for deselection', async () => {
    const statuses = ref<Record<string, CloudUploadTaskStatus>>({})
    const store = {
      tasks: ['T-ONE', 'T-TWO', 'T-THREE'].map(taskId => ({ taskId, status: 'completed', cloudArchived: true }) as TaskSummary),
      deleteTask: vi.fn().mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error('oss_delete_failed')).mockResolvedValueOnce(undefined),
    }
    const deletion = useTaskDeletion(store, statuses)
    await deletion.deleteTasks(['T-ONE', 'T-TWO', 'T-THREE'], 'cloud')
    expect(deletion.deletedTaskIds.value).toEqual(['T-ONE', 'T-THREE'])
    expect(deletion.deletionMessage.value).toContain('成功 2 条，失败 1 条')
    expect(deletion.deletionError.value).toContain('T-TWO')
    expect(statuses.value['T-ONE']?.status).toBe('pending')
    expect(statuses.value['T-TWO']).toBeUndefined()
    expect(deletion.deletionScope.value).toBeNull()
  })

  it('does not delete a collecting or uploading task', async () => {
    const store = {
      tasks: [
        { taskId: 'T-LIVE', status: 'collecting' },
        { taskId: 'T-UPLOADING', status: 'completed' },
      ] as TaskSummary[],
      deleteTask: vi.fn(),
    }
    const deletion = useTaskDeletion(store, ref<Record<string, CloudUploadTaskStatus>>({
      'T-UPLOADING': { status: 'uploading', errorCode: null, retryAt: null },
    }))
    await deletion.deleteTasks(['T-LIVE', 'T-UPLOADING'], 'local')
    expect(store.deleteTask).not.toHaveBeenCalled()
    expect(deletion.deletionMessage.value).toContain('成功 0 条，失败 2 条')
  })
})
