<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import StatusBadge from '@/shared/components/StatusBadge.vue'
import type { CloudUploadTaskStatus, TaskSummary } from '@/types/task'
import { filterTasks } from '../history.filters'

const props = defineProps<{
  tasks: TaskSummary[]
  deletingTaskId?: string | null
  exporting?: boolean
  uploadStatuses: Record<string, CloudUploadTaskStatus>
  requestingUploadId?: string | null
}>()
const emit = defineEmits<{
  open: [taskId: string]
  delete: [task: TaskSummary]
  export: [taskIds: string[]]
  upload: [taskId: string]
}>()

const taskKeyword = ref('')
const participantKeyword = ref('')
const participantNameKeyword = ref('')
const dateKeyword = ref('')
const selectedTaskIds = ref(new Set<string>())
const currentPage = ref(1)
const pageSize = 100

const filtered = computed(() => filterTasks(props.tasks, {
  taskId: taskKeyword.value,
  participantId: participantKeyword.value,
  participantName: participantNameKeyword.value,
  date: dateKeyword.value,
}))
const totalPages = computed(() => Math.max(1, Math.ceil(filtered.value.length / pageSize)))
const paged = computed(() => {
  const offset = (currentPage.value - 1) * pageSize
  return filtered.value.slice(offset, offset + pageSize)
})
const selectableFiltered = computed(() => paged.value.filter((task) => task.status !== 'collecting'))
const selectedCount = computed(() => selectedTaskIds.value.size)
const allFilteredSelected = computed(() => (
  selectableFiltered.value.length > 0
  && selectableFiltered.value.every((task) => selectedTaskIds.value.has(task.taskId))
))
const someFilteredSelected = computed(() => (
  !allFilteredSelected.value
  && selectableFiltered.value.some((task) => selectedTaskIds.value.has(task.taskId))
))

watch(
  () => props.tasks.map((task) => task.taskId),
  (taskIds) => {
    const valid = new Set(taskIds)
    selectedTaskIds.value = new Set(
      [...selectedTaskIds.value].filter((taskId) => valid.has(taskId)),
    )
  },
)

watch(
  [taskKeyword, participantKeyword, participantNameKeyword, dateKeyword],
  () => { currentPage.value = 1 },
)

watch(totalPages, (pages) => {
  if (currentPage.value > pages) currentPage.value = pages
})

function toggleTask(taskId: string): void {
  const next = new Set(selectedTaskIds.value)
  if (next.has(taskId)) next.delete(taskId)
  else next.add(taskId)
  selectedTaskIds.value = next
}

function toggleAllFiltered(): void {
  const next = new Set(selectedTaskIds.value)
  if (allFilteredSelected.value) {
    selectableFiltered.value.forEach((task) => next.delete(task.taskId))
  } else {
    selectableFiltered.value.forEach((task) => next.add(task.taskId))
  }
  selectedTaskIds.value = next
}

function exportSelected(): void {
  if (!selectedTaskIds.value.size || props.exporting) return
  emit('export', props.tasks
    .filter((task) => task.status !== 'collecting' && selectedTaskIds.value.has(task.taskId))
    .map((task) => task.taskId))
}

function taskStatus(value: TaskSummary['status']): string {
  return ({ collecting: '采集中', completed: '已完成', abnormal: '待复核', cancelled: '已取消' })[value]
}
function cloudStatus(task: TaskSummary): CloudUploadTaskStatus | null {
  if (task.status === 'collecting' || task.status === 'cancelled') return null
  if (task.cloudArchived) return { status: 'uploaded', errorCode: null, retryAt: null }
  return props.uploadStatuses[task.taskId] ?? { status: 'pending', errorCode: null, retryAt: null }
}
function cloudStatusText(status: CloudUploadTaskStatus['status']): string {
  return ({ pending: '待上传', uploading: '上传中', failed: '上传失败', uploaded: '已上传' })[status]
}
function cloudStatusTone(status: CloudUploadTaskStatus['status']): 'gray' | 'blue' | 'red' | 'green' {
  const tones: Record<CloudUploadTaskStatus['status'], 'gray' | 'blue' | 'red' | 'green'> = {
    pending: 'gray',
    uploading: 'blue',
    failed: 'red',
    uploaded: 'green',
  }
  return tones[status]
}
function cloudError(code: string | null): string {
  if (!code) return ''
  return ({
    unauthorized: '云端认证失败',
    zip_too_large: '压缩包超过限制',
    missing_files: '本地文件不完整',
    upload_cache_full: '上传缓存空间不足',
    ssh_configuration_missing: '上传配置缺失',
    ssh_connection_failed: '无法连接云服务器',
    cloud_connection_failed: '云服务器连接中断',
  } as Record<string, string>)[code] ?? code
}
function movedBlocks(task: TaskSummary): string {
  const blocks = new Set((task.movementReferences ?? []).map((reference) => (
    reference.blockId
      ?? task.blockBindings?.find((binding) => binding.deviceId === reference.deviceId)?.blockId
      ?? String(reference.deviceId)
  )))
  return blocks.size ? [...blocks].join('、') : '—'
}
</script>

<template>
  <section class="card filter-section">
    <div class="section-head">
      <div><div class="card-title">任务筛选</div><div class="card-subtitle">按任务、体验者编号、姓名和日期查找采集记录</div></div>
      <StatusBadge tone="blue" text="任务记录" />
    </div>
    <div class="filters compact">
      <label>任务编号<input v-model="taskKeyword" placeholder="请输入任务编号" /></label>
      <label>体验者编号<input v-model="participantKeyword" placeholder="请输入体验者编号" /></label>
      <label>体验者姓名<input v-model="participantNameKeyword" placeholder="请输入体验者姓名" /></label>
      <label>日期<input v-model="dateKeyword" type="date" /></label>
    </div>
  </section>

  <section class="card table-card">
    <div class="section-head">
      <div><div class="card-title">历史任务列表</div><div class="card-subtitle">选择任务后可批量导出完整数据记录</div></div>
      <div class="table-head-actions">
        <span v-if="selectedCount" class="selected-count">已选 {{ selectedCount }} 条</span>
        <button class="btn secondary batch-export" :disabled="!selectedCount || exporting" @click="exportSelected">
          {{ exporting ? '正在打包…' : '批量导出' }}
        </button>
      </div>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th class="selection-column"><input class="selection-checkbox" type="checkbox" aria-label="全选当前页" :checked="allFilteredSelected" :indeterminate="someFilteredSelected" :disabled="!selectableFiltered.length" @change="toggleAllFiltered" /></th><th>任务编号</th><th>体验者编号</th><th>姓名</th><th>性别</th><th>年龄</th><th>备注</th><th>体验开始时间</th><th>体验结束时间</th><th>持续时间</th><th>移动积木</th><th>移动次数</th><th>综合评分</th><th>状态</th><th>云端状态</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="task in paged" :key="task.taskId" :class="{ selectedRow: selectedTaskIds.has(task.taskId) }">
            <td class="selection-column"><input class="selection-checkbox" type="checkbox" :aria-label="`选择任务 ${task.taskId}`" :checked="selectedTaskIds.has(task.taskId)" :disabled="task.status === 'collecting'" @change="toggleTask(task.taskId)" /></td>
            <td><button class="link" @click="emit('open', task.taskId)">{{ task.taskId }}</button></td>
            <td>{{ task.participantId }}</td>
            <td>{{ task.participantSnapshot?.name || '—' }}</td>
            <td>{{ task.participantSnapshot?.gender || '—' }}</td>
            <td>{{ task.participantSnapshot?.age ?? '—' }}</td>
            <td>{{ task.note || '—' }}</td>
            <td>{{ task.startTime }}</td>
            <td>{{ task.endTime ?? '—' }}</td>
            <td>{{ task.durationSeconds === null ? '—' : `${task.durationSeconds}秒` }}</td>
            <td>{{ movedBlocks(task) }}</td>
            <td>{{ task.movementReferences?.length ?? 0 }}次</td>
            <td><b>{{ task.totalScore ?? '—' }}</b></td>
            <td><StatusBadge :tone="task.status === 'completed' ? 'green' : 'gray'" :text="taskStatus(task.status)" /></td>
            <td><template v-if="cloudStatus(task)"><StatusBadge :tone="cloudStatusTone(cloudStatus(task)!.status)" :text="cloudStatusText(cloudStatus(task)!.status)" /><small v-if="cloudStatus(task)!.status === 'failed'" class="cloud-error">{{ cloudError(cloudStatus(task)!.errorCode) }}</small></template><span v-else>—</span></td>
            <td><div class="row-actions"><button class="link" @click="emit('open', task.taskId)">查看详情</button><button v-if="task.localAvailable !== false && cloudStatus(task)?.status !== 'uploaded' && cloudStatus(task)" class="link" :disabled="cloudStatus(task)?.status === 'uploading' || requestingUploadId === task.taskId" @click="emit('upload', task.taskId)">{{ cloudStatus(task)?.status === 'failed' ? '重试' : '上传' }}</button><button v-if="task.localAvailable !== false" class="link danger" :disabled="task.status === 'collecting' || cloudStatus(task)?.status === 'uploading' || deletingTaskId === task.taskId" @click="emit('delete', task)">{{ deletingTaskId === task.taskId ? '删除中…' : '删除' }}</button></div></td>
          </tr>
          <tr v-if="!filtered.length"><td class="empty-cell" colspan="16">没有符合筛选条件的任务记录</td></tr>
        </tbody>
      </table>
    </div>
    <div class="pagination">
      <button :disabled="currentPage <= 1" @click="currentPage -= 1">‹</button>
      <span>第 {{ currentPage }} / {{ totalPages }} 页，每页100条，共{{ filtered.length }}条</span>
      <button :disabled="currentPage >= totalPages" @click="currentPage += 1">›</button>
    </div>
  </section>
</template>

<style scoped>
.filter-section{padding:18px}.filter-section .filters{padding:16px 0 0}.filter-section .filters.compact{grid-template-columns:repeat(4,minmax(0,1fr))}.table-card .table-wrap{max-height:430px;overflow:auto;scrollbar-gutter:stable}.table-card th{position:sticky;top:0;z-index:1}.table-head-actions{display:flex;align-items:center;gap:10px}.selected-count{color:#59667a;font-size:11px}.batch-export{padding:8px 13px;font-size:11px}.batch-export:disabled{cursor:not-allowed;opacity:.5}.selection-column{width:38px;padding-right:6px;text-align:center}.selection-checkbox{width:16px;height:16px;margin:0;padding:0;accent-color:var(--primary);cursor:pointer}.selection-checkbox:disabled{cursor:not-allowed;opacity:.45}.cloud-error{display:block;max-width:130px;margin-top:5px;color:#b53f3f;white-space:normal}.empty-cell{text-align:center;color:#7b879a;padding:28px}.row-actions{display:flex;align-items:center;gap:12px;white-space:nowrap}.link.danger{color:#d14343}.link:disabled{cursor:not-allowed;opacity:.45}@media(max-width:1000px){.filter-section .filters.compact{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:680px){.filter-section .filters.compact{grid-template-columns:1fr}.table-card .section-head{align-items:flex-start}.table-head-actions{width:100%;justify-content:space-between}}
</style>
