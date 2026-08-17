<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import StatusBadge from '@/shared/components/StatusBadge.vue'
import { useTaskStore } from '@/stores/task.store'
import HistorySummary from './components/HistorySummary.vue'
import TaskHistorySection from './components/TaskHistorySection.vue'

const router = useRouter()
const taskStore = useTaskStore()
const loading = ref(true)
const errorMessage = ref<string | null>(null)
const deletingTaskId = ref<string | null>(null)

onMounted(async () => {
  loading.value = true
  errorMessage.value = null
  try {
    await taskStore.loadTasks()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error)
  } finally {
    loading.value = false
  }
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

async function deleteTask(task: import('@/types/task').TaskSummary): Promise<void> {
  if (task.status === 'collecting' || deletingTaskId.value) return
  const participant = task.participantSnapshot?.name || task.participantId
  if (!window.confirm(`确定删除任务 ${task.taskId}（${participant}）吗？\n\n该任务记录及其全部移动原始数据将永久删除，无法恢复。`)) return
  deletingTaskId.value = task.taskId
  errorMessage.value = null
  try {
    await taskStore.deleteTask(task.taskId)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error)
  } finally {
    deletingTaskId.value = null
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

    <section v-if="errorMessage" class="card history-notice history-notice--error">
      <strong>历史数据加载失败</strong><span>{{ errorMessage }}</span>
    </section>
    <section v-else-if="loading" class="card history-notice">
      <strong>正在加载历史数据</strong><span>正在获取采集任务记录。</span>
    </section>

    <HistorySummary
      :total="taskStore.tasks.length"
      :latest-count="latestCount"
      :latest-date="latestDate"
      :average-score="taskStore.averageScore"
      :completed-count="taskStore.completedCount"
    />
    <TaskHistorySection :tasks="taskStore.tasks" :deleting-task-id="deletingTaskId" @open="openTaskResult" @delete="deleteTask" />
  </div>
</template>

<style scoped>
.history-page{gap:16px}.history-eyebrow{display:flex;align-items:center;gap:9px;margin-bottom:9px;color:#7b879a;font-size:12px}.history-page .page-heading .btn:disabled{cursor:not-allowed;opacity:.5}.history-page :deep(.summary-grid){gap:12px}.history-page :deep(.summary){padding:16px 18px}.history-page :deep(.summary b){font-size:26px}.history-notice{padding:18px;display:flex;gap:10px;align-items:center;color:#59667a}.history-notice span{font-size:12px}.history-notice--error{border-color:#f2caca;background:#fff8f8;color:#b53f3f}@media(max-width:680px){.history-eyebrow{align-items:flex-start}.history-notice{align-items:flex-start;flex-direction:column}}
</style>
