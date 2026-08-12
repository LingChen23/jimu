<script setup lang="ts">
import { computed, ref } from 'vue'
import StatusBadge from '@/shared/components/StatusBadge.vue'
import type { TaskSummary } from '@/types/task'
import { filterTasks } from '../history.filters'

const props = defineProps<{ tasks: TaskSummary[] }>()
const emit = defineEmits<{ open: [taskId: string] }>()

const taskKeyword = ref('')
const participantKeyword = ref('')
const statusKeyword = ref<'all' | TaskSummary['status']>('all')
const dateKeyword = ref('')

const filtered = computed(() => filterTasks(props.tasks, {
  taskId: taskKeyword.value,
  participantId: participantKeyword.value,
  status: statusKeyword.value,
  date: dateKeyword.value,
}))

function quality(value: TaskSummary['dataQuality']): string {
  return ({ complete: '完整', good: '良好', normal: '一般', poor: '较差' })[value]
}

function taskStatus(value: TaskSummary['status']): string {
  return ({ collecting: '采集中', completed: '已完成', abnormal: '待复核', cancelled: '已取消' })[value]
}
</script>

<template>
  <section class="card filter-section">
    <div class="section-head">
      <div><div class="card-title">任务筛选</div><div class="card-subtitle">按任务、体验者、状态和日期查找采集记录</div></div>
      <StatusBadge tone="blue" text="任务记录" />
    </div>
    <div class="filters">
      <label>任务编号<input v-model="taskKeyword" placeholder="请输入任务编号" /></label>
      <label>体验者编号<input v-model="participantKeyword" placeholder="请输入体验者编号" /></label>
      <label>任务状态<select v-model="statusKeyword"><option value="all">全部状态</option><option value="collecting">采集中</option><option value="completed">已完成</option><option value="cancelled">已取消</option></select></label>
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
        <thead><tr><th>任务编号</th><th>体验者编号</th><th>备注</th><th>体验开始时间</th><th>体验结束时间</th><th>持续时间</th><th>综合评分</th><th>数据质量</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="task in filtered" :key="task.taskId">
            <td><button class="link" @click="emit('open', task.taskId)">{{ task.taskId }}</button></td>
            <td>{{ task.participantId }}</td>
            <td>{{ task.note || task.taskType || '—' }}</td>
            <td>{{ task.startTime }}</td>
            <td>{{ task.endTime ?? '—' }}</td>
            <td>{{ task.durationSeconds === null ? '—' : `${task.durationSeconds}秒` }}</td>
            <td><b>{{ task.totalScore ?? '—' }}</b></td>
            <td><StatusBadge :tone="task.dataQuality === 'complete' ? 'green' : task.dataQuality === 'good' ? 'blue' : task.dataQuality === 'normal' ? 'orange' : 'red'" :text="quality(task.dataQuality)" /></td>
            <td><StatusBadge :tone="task.status === 'completed' ? 'green' : 'gray'" :text="taskStatus(task.status)" /></td>
            <td><button class="link" @click="emit('open', task.taskId)">查看详情</button></td>
          </tr>
          <tr v-if="!filtered.length"><td class="empty-cell" colspan="10">没有符合筛选条件的任务记录</td></tr>
        </tbody>
      </table>
    </div>
    <div class="pagination"><button class="active">1</button><span>共{{ filtered.length }}条</span></div>
  </section>
</template>

<style scoped>
.filter-section{padding:18px}.filter-section .filters{padding:16px 0 0}.table-card .table-wrap{max-height:430px;overflow:auto;scrollbar-gutter:stable}.table-card th{position:sticky;top:0;z-index:1}.empty-cell{text-align:center;color:#7b879a;padding:28px}
</style>
