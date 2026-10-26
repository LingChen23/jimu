<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import StatusBadge from '@/shared/components/StatusBadge.vue'
import { motionDataService } from '@/services/data.service'
import { useTaskStore } from '@/stores/task.store'
import type { CloudUploadTaskStatus } from '@/types/task'
import HistorySummary from './components/HistorySummary.vue'
import TaskHistorySection from './components/TaskHistorySection.vue'
import { useTaskDeletion } from './use-task-deletion'

const router = useRouter()
const taskStore = useTaskStore()
const loading = ref(true)
const errorMessage = ref<string | null>(null)
const cloudMessage = ref<string | null>(null)
const exportingTasks = ref(false)
const requestingUploadId = ref<string | null>(null)
const uploadStatuses = ref<Record<string, CloudUploadTaskStatus>>({})
const { deletingTaskId, deletionScope, deletedTaskIds, deletionMessage, deletionError, deleteTasks } = useTaskDeletion(taskStore, uploadStatuses)
let uploadStatusTimer: number | undefined

async function refreshUploadStatuses(reportError = false): Promise<void> {
  try {
    uploadStatuses.value = (await motionDataService.getCloudUploadStatus()).tasks
  } catch (error) {
    if (reportError) errorMessage.value = error instanceof Error ? error.message : String(error)
  }
}

onMounted(async () => {
  loading.value = true
  errorMessage.value = null
  try {
    await taskStore.loadTasks()
    void taskStore.prefetchTaskResults(taskStore.tasks.slice(0, 5).map(task => task.taskId))
    await refreshUploadStatuses(true)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error)
  } finally {
    loading.value = false
  }
  try {
    await taskStore.mergeCloudTasks()
  } catch (error) {
    cloudMessage.value = `云端记录暂不可用：${error instanceof Error ? error.message : String(error)}`
  }
  uploadStatusTimer = window.setInterval(() => { void refreshUploadStatuses() }, 3_000)
})

onUnmounted(() => {
  if (uploadStatusTimer !== undefined) window.clearInterval(uploadStatusTimer)
  taskStore.cancelCloudTaskLoading()
})

const latestDate = computed(() => taskStore.tasks
  .map((task) => task.startTime.slice(0, 10))
  .sort()
  .at(-1) ?? '—')
const latestCount = computed(() => taskStore.tasks
  .filter((task) => task.startTime.startsWith(latestDate.value)).length)

function openTaskResult(taskId: string): void {
  void router.push({ name: 'task-result', params: { taskId } })
}

async function exportTasks(taskIds: string[]): Promise<void> {
  if (!taskIds.length || exportingTasks.value) return
  exportingTasks.value = true
  errorMessage.value = null
  try {
    const exported = await taskStore.exportTasks(taskIds)
    const url = URL.createObjectURL(exported.content)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = exported.fileName
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 0)
  } catch (error) {
    errorMessage.value = `批量导出失败：${error instanceof Error ? error.message : String(error)}`
  } finally {
    exportingTasks.value = false
  }
}

async function uploadTask(taskId: string): Promise<void> {
  if (requestingUploadId.value) return
  requestingUploadId.value = taskId
  errorMessage.value = null
  try {
    await motionDataService.enqueueCloudUpload([taskId])
    await refreshUploadStatuses(true)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error)
  } finally {
    requestingUploadId.value = null
  }
}

</script>

<template>
  <div class="page-stack history-page">
    <section class="page-heading">
      <div>
        <div class="history-eyebrow"><StatusBadge tone="green" text="采集记录" /></div>
        <h1>历史记录</h1>
        <p>查看采集任务记录。</p>
      </div>
    </section>

    <section v-if="errorMessage || deletionError" class="card history-notice history-notice--error">
      <strong>操作失败</strong><span>{{ errorMessage || deletionError }}</span>
    </section>
    <section v-else-if="loading" class="card history-notice">
      <strong>正在加载历史数据</strong><span>正在获取采集任务记录。</span>
    </section>
    <section v-if="deletionMessage" class="card history-notice" role="status">{{ deletionMessage }}</section>
    <section v-if="cloudMessage" class="card history-notice"><strong>本地记录已加载</strong><span>{{ cloudMessage }}</span></section>

    <section v-if="taskStore.cloudLoading && !cloudMessage" class="card history-notice">
      <strong>云端记录分批加载中</strong>
      <span>已加载 {{ taskStore.cloudLoadedCount }} / {{ taskStore.cloudTotal || '…' }} 条，每批10条</span>
    </section>

    <HistorySummary
      :total="taskStore.tasks.length"
      :latest-count="latestCount"
      :latest-date="latestDate"
      :average-score="taskStore.averageScore"
      :completed-count="taskStore.completedCount"
    />
    <TaskHistorySection
      :tasks="taskStore.tasks"
      :deleting-task-id="deletingTaskId"
      :deletion-scope="deletionScope"
      :deleted-task-ids="deletedTaskIds"
      :exporting="exportingTasks"
      :upload-statuses="uploadStatuses"
      :requesting-upload-id="requestingUploadId"
      @open="openTaskResult"
      @delete="(task, scope) => deleteTasks([task.taskId], scope)"
      @delete-batch="deleteTasks"
      @export="exportTasks"
      @upload="uploadTask"
    />
  </div>
</template>

<style scoped>
.history-page{gap:16px}.history-eyebrow{display:flex;align-items:center;gap:9px;margin-bottom:9px;color:#7b879a;font-size:12px}.history-page .page-heading .btn:disabled{cursor:not-allowed;opacity:.5}.history-page :deep(.summary-grid){gap:12px}.history-page :deep(.summary){padding:16px 18px}.history-page :deep(.summary b){font-size:26px}.history-notice{padding:18px;display:flex;gap:10px;align-items:center;color:#59667a}.history-notice span{font-size:12px}.history-notice--error{border-color:#f2caca;background:#fff8f8;color:#b53f3f}@media(max-width:680px){.history-eyebrow{align-items:flex-start}.history-notice{align-items:flex-start;flex-direction:column}}
</style>
