import { ref, type Ref } from 'vue'
import type { CloudUploadTaskStatus, TaskSummary } from '@/types/task'

type Scope = 'local' | 'cloud'
interface TaskDeletionStore {
  tasks: TaskSummary[]
  deleteTask(taskId: string, scope: Scope, keepCloudRecord: boolean): Promise<void>
}

const errorReasons: Record<string, string> = {
  cloud_connection_failed: '云端连接失败，请重试',
  cloud_upload_disabled: '未配置云端连接',
  unauthorized: '云端认证失败',
  archive_busy: '云端正在归档，请稍后重试',
  oss_delete_failed: '云端文件删除失败，请重试',
  cloud_http_405: '云端尚未更新删除功能',
  cloud_http_404: '云端尚未更新删除功能',
}

export function useTaskDeletion(store: TaskDeletionStore, statuses: Ref<Record<string, CloudUploadTaskStatus>>) {
  const deletingTaskId = ref<string | null>(null)
  const deletionScope = ref<Scope | null>(null)
  const deletedTaskIds = ref<string[]>([])
  const deletionMessage = ref<string | null>(null)
  const deletionError = ref<string | null>(null)

  async function deleteTasks(taskIds: string[], scope: Scope): Promise<void> {
    if (!taskIds.length || deletionScope.value) return
    deletionScope.value = scope
    deletedTaskIds.value = []
    deletionMessage.value = null
    deletionError.value = null
    const failures: string[] = []
    try {
      for (const taskId of new Set(taskIds)) {
        deletingTaskId.value = taskId
        const task = store.tasks.find(item => item.taskId === taskId)
        const status = statuses.value[taskId]?.status
        if (!task || task.status === 'collecting' || status === 'uploading') {
          failures.push(`${taskId}：任务正在采集或上传，请稍后再试`)
          continue
        }
        try {
          await store.deleteTask(taskId, scope, task.cloudArchived === true || status === 'uploaded')
          if (scope === 'cloud') {
            statuses.value[taskId] = { status: 'pending', errorCode: null, retryAt: null }
          }
          deletedTaskIds.value = [...deletedTaskIds.value, taskId]
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error)
          failures.push(`${taskId}：${errorReasons[message] ?? message}`)
        }
      }
      deletionMessage.value = `删除${scope === 'local' ? '本地' : '云端'}：成功 ${deletedTaskIds.value.length} 条，失败 ${failures.length} 条。`
      if (failures.length) deletionError.value = failures.slice(0, 3).join('；')
    } finally {
      deletingTaskId.value = null
      deletionScope.value = null
    }
  }

  return { deletingTaskId, deletionScope, deletedTaskIds, deletionMessage, deletionError, deleteTasks }
}
