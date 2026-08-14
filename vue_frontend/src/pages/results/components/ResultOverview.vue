<script setup lang="ts">
import { computed } from 'vue'
import StatusBadge from '@/shared/components/StatusBadge.vue'
import type { DeviceSnapshot } from '@/types/device'
import type { AnalysisMode, MovementDetail } from '@/types/motion'
import type { TaskMovementReference, TaskResult, TaskSummary } from '@/types/task'

const props = defineProps<{
  resultKind: 'task' | 'movement'
  taskResult: TaskResult | null
  task: TaskSummary | null
  device: DeviceSnapshot | null
  movement: MovementDetail | null
  movementReference: TaskMovementReference | null
  enrichmentNotice: string | null
}>()

function taskStatus(value: TaskSummary['status'] | undefined): string {
  if (!value) return props.movement?.state === 'completed' ? '移动已完成' : '移动进行中'
  return ({ collecting: '采集中', completed: '已完成', abnormal: '待复核', cancelled: '已取消' })[value]
}
function analysisMode(value: AnalysisMode | undefined): string {
  if (!value) return '—'
  return value === 'pc' ? 'PC 独立判定' : '设备状态驱动'
}
function dateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', { hour12: false })
}
function durationText(): string {
  const seconds = props.task?.durationSeconds ?? props.movement?.durationSeconds
  return seconds === null || seconds === undefined ? '—' : `${seconds}秒`
}
const displayedScore = computed(() => props.resultKind === 'task'
  ? props.movementReference?.score ?? null
  : props.taskResult?.score.totalScore ?? null)
</script>

<template>
  <section class="card result-info result-info--review">
    <div><span>详情类型</span><b>{{ resultKind === 'task' ? '任务结果' : '移动结果' }}</b></div>
    <div><span>任务编号</span><b>{{ task?.taskId ?? '—（未关联任务）' }}</b></div>
    <div><span>体验者编号</span><b>{{ task?.participantId ?? '—（未关联任务）' }}</b></div>
    <div><span>姓名</span><b>{{ task?.participantSnapshot?.name || '—' }}</b></div>
    <div><span>性别</span><b>{{ task?.participantSnapshot?.gender || '—' }}</b></div>
    <div><span>年龄</span><b>{{ task?.participantSnapshot?.age ?? '—' }}</b></div>
    <div><span>文化程度</span><b>{{ task?.participantSnapshot?.educationLevel || '—' }}</b></div>
    <div><span>利手</span><b>{{ task?.participantSnapshot?.dominantHand || '—' }}</b></div>
    <div><span>备注</span><b>{{ task?.note || '—（未填写）' }}</b></div>
    <div><span>硬件设备ID</span><b>{{ movement?.deviceId ?? task?.deviceId ?? '—' }}</b></div>
    <div><span>积木编号</span><b>{{ movement ? (device?.blockId ?? '—') : (task?.blockId ?? '—') }}</b></div>
    <div><span>移动编号</span><b>{{ movement ? `M${movement.movementId}` : '—（未关联移动）' }}</b></div>
    <div><span>分析模式</span><b>{{ analysisMode(movement?.analysisMode) }}</b></div>
    <div><span>开始时间</span><b>{{ dateTime(task?.startTime ?? movement?.startedAt) }}</b></div>
    <div><span>结束时间</span><b>{{ dateTime(task?.endTime ?? movement?.endedAt) }}</b></div>
    <div><span>持续时间</span><b>{{ durationText() }}</b></div>
    <div><span>记录状态</span><b>{{ taskStatus(task?.status) }}</b></div>
  </section>

  <section v-if="enrichmentNotice" class="card result-source-note">
    <StatusBadge tone="gray" text="数据提示" />
    <span>{{ enrichmentNotice }}</span>
  </section>

  <section class="card score result-score-card">
      <span>{{ resultKind === 'task' ? '本次移动评分' : '评分' }}</span>
      <strong>{{ displayedScore ?? '—' }}<small v-if="displayedScore !== null">分</small></strong>
      <StatusBadge :tone="displayedScore !== null ? 'green' : 'gray'" :text="displayedScore !== null ? '已评分' : '未评分'" />
  </section>
</template>

<style scoped>
.result-info--review{grid-template-columns:repeat(4,minmax(0,1fr))}.result-info--review b{overflow-wrap:anywhere}.result-source-note{padding:14px 18px;display:flex;align-items:center;gap:10px;color:#68758b;font-size:12px}.result-score-card{min-height:150px}@media(max-width:1350px){.result-info--review{grid-template-columns:repeat(3,1fr)}}@media(max-width:680px){.result-info--review{grid-template-columns:1fr}.result-source-note{align-items:flex-start;flex-direction:column}}
</style>
