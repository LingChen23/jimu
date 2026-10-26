<script setup lang="ts">
import { blockName } from '@/config/system'
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import StatusBadge from '@/shared/components/StatusBadge.vue'
import HistorySummary from '@/pages/history/components/HistorySummary.vue'
import { fetchWithTimeout } from '@/services/http-client'
import { taskSummary } from '@/services/motion-system-data.service'
import { useTaskStore } from '@/stores/task.store'
import type { TaskSummary } from '@/types/task'
import { deleteArchivedTasks, historyQuery, type CloudHistoryFilters } from './cloud-history'

type Statistics = { total: number; average_score: number | null; latest_date: string | null; latest_count: number }
const router = useRouter()
const taskStore = useTaskStore()
const emptyFilters = (): CloudHistoryFilters => ({ taskId: '', participantId: '', participantName: '', date: '' })
const filters = reactive(emptyFilters())
let appliedFilters = emptyFilters()
const tasks = ref<TaskSummary[]>([])
const page = ref(1)
const pageSize = 100
const batchSize = 10
const batchesPerPage = pageSize / batchSize
const total = ref(0)
const statistics = ref<Statistics>({ total: 0, average_score: null, latest_date: null, latest_count: 0 })
const loading = ref(false)
const exporting = ref(false)
const errorMessage = ref('')
const selected = ref(new Set<string>())
const deleting = ref(false)
const deletionMessage = ref('')
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
const selectable = computed(() => tasks.value.filter(task => task.status !== 'collecting'))
const allSelected = computed(() => selectable.value.length > 0 && selectable.value.every(task => selected.value.has(task.taskId)))

async function load(targetPage = page.value): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  selected.value = new Set()
  tasks.value = []
  try {
    const firstBatch = (targetPage - 1) * batchesPerPage + 1
    for (let offset = 0; offset < batchesPerPage; offset += 1) {
      const response = await fetchWithTimeout(
        `/api/history/tasks?${historyQuery(appliedFilters, firstBatch + offset, batchSize)}`,
        {},
        30_000,
      )
      const payload = await response.json()
      if (!response.ok) throw new Error(String(payload?.error?.code ?? `HTTP ${response.status}`))
      const batch = (payload.items ?? []).map((item: any) => item.task
        ? taskSummary(item.task)
        : taskSummary({ ...item, status: item.task_status, movement_references: [] }))
      const merged = new Map(tasks.value.map(task => [task.taskId, task]))
      batch.forEach((task: TaskSummary) => merged.set(task.taskId, task))
      tasks.value = [...merged.values()]
      page.value = targetPage
      total.value = Number(payload.total || 0)
      statistics.value = payload.statistics ?? statistics.value
      if (offset === 0) {
        void taskStore.prefetchTaskResults(tasks.value.slice(0, 5).map(task => task.taskId))
      }
      const pageAvailable = Math.max(0, Math.min(pageSize, total.value - (targetPage - 1) * pageSize))
      if (!batch.length || tasks.value.length >= pageAvailable) break
    }
  } catch (error) { errorMessage.value = error instanceof Error ? error.message : String(error) }
  finally { loading.value = false }
}
function search(): void { if (deleting.value) return; appliedFilters = { ...filters }; void load(1) }
function reset(): void { if (deleting.value) return; Object.assign(filters, emptyFilters()); appliedFilters = emptyFilters(); void load(1) }
function changePage(next: number): void { if (!loading.value && !deleting.value && next >= 1 && next <= totalPages.value) void load(next) }
function toggle(taskId: string): void { const next = new Set(selected.value); next.has(taskId) ? next.delete(taskId) : next.add(taskId); selected.value = next }
function toggleAll(): void { selected.value = allSelected.value ? new Set() : new Set(selectable.value.map(task => task.taskId)) }
function movedBlocks(task: TaskSummary): string {
  const blocks = new Set((task.movementReferences ?? []).map(reference => reference.blockId
    ?? task.blockBindings?.find(binding => binding.deviceId === reference.deviceId)?.blockId
    ?? String(reference.deviceId)))
  return blocks.size ? [...blocks].map(blockName).join('、') : '—'
}
function taskStatus(value: TaskSummary['status']): string { return ({ collecting: '采集中', completed: '已完成', abnormal: '待复核', cancelled: '已取消' })[value] }
function open(taskId: string): void { void router.push({ name: 'cloud-task-result', params: { taskId } }) }
function download(taskId: string): void { window.location.href = `/api/history/tasks/${encodeURIComponent(taskId)}/download` }
async function exportSelected(): Promise<void> {
  if (!selected.value.size || exporting.value || deleting.value) return
  exporting.value = true; errorMessage.value = ''
  try {
    const response = await fetchWithTimeout('/api/history/tasks/export-batch', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ task_ids: [...selected.value] }),
    }, 1_800_000)
    if (!response.ok) throw new Error(`批量导出失败：${response.status}`)
    const url = URL.createObjectURL(await response.blob())
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'tasks_batch.zip'
    document.body.appendChild(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 0)
  } catch (error) { errorMessage.value = error instanceof Error ? error.message : String(error) }
  finally { exporting.value = false }
}
onMounted(() => { void load(1) })

async function deleteTasks(taskIds: string[]): Promise<void> {
  if (!taskIds.length || deleting.value || loading.value || exporting.value) return
  deleting.value = true
  errorMessage.value = ''; deletionMessage.value = ''
  try {
    const result = await deleteArchivedTasks(taskIds, taskId => {
      tasks.value = tasks.value.filter(task => task.taskId !== taskId)
      total.value = Math.max(0, total.value - 1)
      statistics.value.total = Math.max(0, statistics.value.total - 1)
      const remaining = new Set(selected.value); remaining.delete(taskId); selected.value = remaining
      taskStore.forgetTaskResult(taskId)
    })
    deletionMessage.value = `删除云端：成功 ${result.deleted.length} 条，失败 ${result.failures.length} 条。`
    if (result.deleted.length) {
      if (!tasks.value.length && page.value > totalPages.value) {
        await load(totalPages.value)
      } else {
        try {
          const response = await fetchWithTimeout('/api/history/tasks?page=1&page_size=1', {}, 30_000)
          const payload = await response.json()
          if (!response.ok) throw new Error(`HTTP ${response.status}`)
          statistics.value = payload.statistics ?? statistics.value
        } catch {
          deletionMessage.value += ' 统计暂未更新，可稍后查询。'
        }
      }
    }
    if (result.failures.length) errorMessage.value = result.failures.slice(0, 3).map(item => `${item.taskId}：${item.message}`).join('；')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error)
  } finally { deleting.value = false }
}
</script>

<template>
  <div class="cloud-history-shell">
    <header class="cloud-header"><div class="cloud-brand"><div class="cloud-brand-mark">积</div><div><strong>积木运动数据云端</strong><span>历史归档记录</span></div></div><StatusBadge tone="green" text="云端服务" /></header>
    <main class="cloud-content page-stack">
      <section class="page-heading"><div><div class="history-eyebrow"><StatusBadge tone="green" text="归档记录" /></div><h1>历史记录</h1><p>查看已经归档到 OSS 的采集任务记录。</p></div></section>
      <section v-if="errorMessage" class="card history-notice history-notice--error"><strong>操作失败</strong><span>{{ errorMessage }}</span></section>
      <HistorySummary
        :total="statistics.total"
        :latest-count="statistics.latest_count"
        :latest-date="statistics.latest_date || '—'"
        :average-score="statistics.average_score"
        :completed-count="statistics.total"
        total-caption="云端归档索引"
        latest-label="最新任务"
        average-caption="已评分归档任务"
        completed-label="已归档任务数"
        completed-caption="OSS 归档完成"
      />
      <form class="card filter-section" @submit.prevent="search">
        <div class="section-head"><div><div class="card-title">任务筛选</div><div class="card-subtitle">按任务、体验者编号、姓名和日期查找归档记录</div></div><StatusBadge tone="blue" text="任务记录" /></div>
        <div class="filters compact"><label>任务编号<input v-model="filters.taskId" placeholder="请输入任务编号" /></label><label>体验者编号<input v-model="filters.participantId" placeholder="请输入体验者编号" /></label><label>体验者姓名<input v-model="filters.participantName" placeholder="请输入体验者姓名" /></label><label>日期<input v-model="filters.date" type="date" /></label></div>
        <div class="filter-actions"><button class="btn primary" type="submit" :disabled="loading || deleting">查询</button><button class="btn secondary" type="button" :disabled="loading || deleting" @click="reset">重置</button></div>
      </form>
      <section class="card table-card">
        <div class="section-head"><div><div class="card-title">历史任务列表</div><div class="card-subtitle" role="status" aria-live="polite">{{ deletionMessage || '删除只影响云端归档，不删除 PC 本地数据；点击后直接执行' }}</div></div><div class="table-head-actions"><span v-if="selected.size">已选 {{ selected.size }} 条</span><button class="btn secondary" :disabled="!selected.size || exporting || deleting" @click="exportSelected">{{ exporting ? '正在打包…' : '批量导出' }}</button><button class="link delete-action" :disabled="!selected.size || loading || exporting || deleting" @click="deleteTasks([...selected])">{{ deleting ? '删除中…' : '批量删除' }}</button></div></div>
        <div class="table-wrap"><table><thead><tr><th><input type="checkbox" :checked="allSelected" :disabled="!selectable.length || deleting" @change="toggleAll" /></th><th>任务编号</th><th>体验者编号</th><th>姓名</th><th>性别</th><th>年龄</th><th>备注</th><th>体验开始时间</th><th>体验结束时间</th><th>持续时间</th><th>移动积木</th><th>移动次数</th><th>综合评分</th><th>状态</th><th>云端状态</th><th>操作</th></tr></thead>
          <tbody><tr v-for="task in tasks" :key="task.taskId"><td><input type="checkbox" :checked="selected.has(task.taskId)" :disabled="deleting" @change="toggle(task.taskId)" /></td><td><button class="link" @click="open(task.taskId)">{{ task.taskId }}</button></td><td>{{ task.participantId }}</td><td>{{ task.participantSnapshot?.name || '—' }}</td><td>{{ task.participantSnapshot?.gender || '—' }}</td><td>{{ task.participantSnapshot?.age ?? '—' }}</td><td>{{ task.note || '—' }}</td><td>{{ task.startTime }}</td><td>{{ task.endTime || '—' }}</td><td>{{ task.durationSeconds === null ? '—' : `${task.durationSeconds}秒` }}</td><td>{{ movedBlocks(task) }}</td><td>{{ task.movementReferences?.length ?? 0 }}次</td><td><b>{{ task.totalScore ?? '—' }}</b></td><td><StatusBadge :tone="task.status === 'completed' ? 'green' : 'gray'" :text="taskStatus(task.status)" /></td><td><StatusBadge tone="green" text="已上传" /></td><td><div class="row-actions"><button class="link" @click="open(task.taskId)">查看详情</button><button class="link" @click="download(task.taskId)">下载 ZIP</button><button class="link delete-action" :disabled="loading || exporting || deleting" @click="deleteTasks([task.taskId])">删除</button></div></td></tr><tr v-if="loading"><td colspan="16" class="empty-cell">正在加载云端历史记录…</td></tr><tr v-else-if="!tasks.length"><td colspan="16" class="empty-cell">没有符合条件的归档任务</td></tr></tbody></table></div>
        <div class="pagination"><button :disabled="page <= 1 || loading || deleting" @click="changePage(page - 1)">‹</button><span>第 {{ page }} / {{ totalPages }} 页，共 {{ total }} 条</span><button :disabled="page >= totalPages || loading || deleting" @click="changePage(page + 1)">›</button></div>
      </section>
    </main>
  </div>
</template>

<style scoped>
.table-card thead th:last-child{position:sticky;right:0;z-index:3;background:#f8fafc;box-shadow:-5px 0 8px -6px #68758b}
.table-card tbody tr>td:last-child:not(.empty-cell){position:sticky;right:0;z-index:2;background:#fff;box-shadow:-5px 0 8px -6px #68758b}
.table-head-actions .delete-action:disabled{opacity:.65}
.cloud-history-shell{min-height:100vh;background:#f3f6fa}.cloud-header{height:72px;padding:0 32px;display:flex;align-items:center;justify-content:space-between;background:linear-gradient(135deg,#082849,#0b3c70);color:#fff}.cloud-brand{display:flex;align-items:center;gap:12px}.cloud-brand-mark{width:40px;height:40px;border-radius:12px;display:grid;place-items:center;background:linear-gradient(135deg,#2aa5ff,#2462e6);font-weight:900}.cloud-brand strong,.cloud-brand span{display:block}.cloud-brand span{margin-top:3px;color:#b9cee6;font-size:12px}.cloud-content{max-width:1500px;margin:0 auto;padding:26px}.history-eyebrow{margin-bottom:9px}.history-notice{padding:18px;display:flex;gap:10px}.history-notice--error{border-color:#f2caca;background:#fff8f8;color:#b53f3f}.filter-section{padding:18px}.filter-section .filters{padding:16px 0 0;grid-template-columns:repeat(4,minmax(0,1fr))}.filter-actions{display:flex;justify-content:flex-end;gap:10px}.table-wrap{max-height:520px;overflow:auto}.table-card th{position:sticky;top:0;z-index:1}.table-head-actions,.row-actions{display:flex;align-items:center;gap:12px;white-space:nowrap}.delete-action{color:#b53f3f}.delete-action:disabled{cursor:not-allowed;opacity:.4}.table-head-actions{flex-wrap:wrap;justify-content:flex-end}.empty-cell{text-align:center;color:#7b879a;padding:28px}@media(max-width:900px){.filter-section .filters{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:680px){.cloud-header{padding:0 16px}.cloud-content{padding:16px}.filter-section .filters{grid-template-columns:1fr}}
</style>
