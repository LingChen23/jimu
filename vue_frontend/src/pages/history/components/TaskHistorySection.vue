<script setup lang="ts">
import { computed, ref } from 'vue'
import StatusBadge from '@/shared/components/StatusBadge.vue'
import type { TaskSummary } from '@/types/task'
import { filterTasks } from '../history.filters'

const props = defineProps<{ tasks: TaskSummary[]; deletingTaskId?: string | null }>()
const emit = defineEmits<{ open: [taskId: string]; delete: [task: TaskSummary] }>()

const taskKeyword = ref('')
const participantKeyword = ref('')
const dateKeyword = ref('')

const filtered = computed(() => filterTasks(props.tasks, {
  taskId: taskKeyword.value,
  participantId: participantKeyword.value,
  date: dateKeyword.value,
}))

function taskStatus(value: TaskSummary['status']): string {
  return ({ collecting: '采集中', completed: '已完成', abnormal: '待复核', cancelled: '已取消' })[value]
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
      <div><div class="card-title">任务筛选</div><div class="card-subtitle">按任务、体验者和日期查找采集记录</div></div>
      <StatusBadge tone="blue" text="任务记录" />
    </div>
    <div class="filters compact">
      <label>任务编号<input v-model="taskKeyword" placeholder="请输入任务编号" /></label>
      <label>体验者编号<input v-model="participantKeyword" placeholder="请输入体验者编号" /></label>
      <label>日期<input v-model="dateKeyword" type="date" /></label>
    </div>
  </section>

  <section class="card table-card">
    <div class="section-head">
      <div><div class="card-title">历史任务列表</div></div>
      <StatusBadge tone="blue" text="任务记录" />
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>任务编号</th><th>体验者编号</th><th>姓名</th><th>性别</th><th>年龄</th><th>备注</th><th>体验开始时间</th><th>体验结束时间</th><th>持续时间</th><th>移动积木</th><th>移动次数</th><th>综合评分</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="task in filtered" :key="task.taskId">
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
            <td><div class="row-actions"><button class="link" @click="emit('open', task.taskId)">查看详情</button><button class="link danger" :disabled="task.status === 'collecting' || deletingTaskId === task.taskId" @click="emit('delete', task)">{{ deletingTaskId === task.taskId ? '删除中…' : '删除' }}</button></div></td>
          </tr>
          <tr v-if="!filtered.length"><td class="empty-cell" colspan="14">没有符合筛选条件的任务记录</td></tr>
        </tbody>
      </table>
    </div>
    <div class="pagination"><button class="active">1</button><span>共{{ filtered.length }}条</span></div>
  </section>
</template>

<style scoped>
.filter-section{padding:18px}.filter-section .filters{padding:16px 0 0}.table-card .table-wrap{max-height:430px;overflow:auto;scrollbar-gutter:stable}.table-card th{position:sticky;top:0;z-index:1}.empty-cell{text-align:center;color:#7b879a;padding:28px}.row-actions{display:flex;align-items:center;gap:12px;white-space:nowrap}.link.danger{color:#d14343}.link:disabled{cursor:not-allowed;opacity:.45}
</style>
