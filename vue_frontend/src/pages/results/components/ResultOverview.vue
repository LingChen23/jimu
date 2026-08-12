<script setup lang="ts">
import StatusBadge from '@/shared/components/StatusBadge.vue'
import type { DeviceSnapshot } from '@/types/device'
import type { AnalysisMode, MovementDetail } from '@/types/motion'
import type { TaskResult, TaskSummary } from '@/types/task'

const props = defineProps<{
  resultKind: 'task' | 'movement'
  taskResult: TaskResult | null
  task: TaskSummary | null
  device: DeviceSnapshot | null
  movement: MovementDetail | null
  usesMockMovement: boolean
  enrichmentNotice: string | null
}>()

function quality(value: TaskSummary['dataQuality'] | undefined): string {
  if (!value) return '未关联任务'
  return ({ complete: '完整', good: '良好', normal: '一般', poor: '较差' })[value]
}
function qualityTone(value: TaskSummary['dataQuality'] | undefined): 'green' | 'blue' | 'orange' | 'red' | 'gray' {
  if (!value) return 'gray'
  return value === 'complete' ? 'green' : value === 'good' ? 'blue' : value === 'normal' ? 'orange' : 'red'
}
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
</script>

<template>
  <section class="card result-info result-info--review">
    <div><span>详情类型</span><b>{{ resultKind === 'task' ? '任务结果' : '移动结果' }}</b></div>
    <div><span>任务编号</span><b>{{ task?.taskId ?? '—（未关联任务）' }}</b></div>
    <div><span>体验者编号</span><b>{{ task?.participantId ?? '—（未关联任务）' }}</b></div>
    <div><span>备注</span><b>{{ task?.note || task?.taskType || '—（未填写）' }}</b></div>
    <div><span>硬件设备ID</span><b>{{ task?.deviceId ?? movement?.deviceId ?? '—' }}</b></div>
    <div><span>移动编号</span><b>{{ movement ? `M${movement.movementId}` : '—（未关联移动）' }}</b></div>
    <div><span>分析模式</span><b>{{ analysisMode(movement?.analysisMode) }}</b></div>
    <div><span>开始时间</span><b>{{ dateTime(task?.startTime ?? movement?.startedAt) }}</b></div>
    <div><span>结束时间</span><b>{{ dateTime(task?.endTime ?? movement?.endedAt) }}</b></div>
    <div><span>持续时间</span><b>{{ durationText() }}</b></div>
    <div><span>数据质量</span><b><StatusBadge :tone="qualityTone(task?.dataQuality)" :text="quality(task?.dataQuality)" /></b></div>
    <div><span>记录状态</span><b>{{ taskStatus(task?.status) }}</b></div>
  </section>

  <section v-if="usesMockMovement || enrichmentNotice" class="card result-source-note">
    <StatusBadge :tone="usesMockMovement ? 'orange' : 'gray'" :text="usesMockMovement ? 'Mock 辅助字段' : '待接入'" />
    <span>{{ usesMockMovement ? '速度、姿态、轨迹和震颤来自同设备最近完成的 Mock 移动记录；任务—移动关联待接入。' : enrichmentNotice }}</span>
  </section>

  <section class="score-grid">
    <div class="card score">
      <span>综合评分</span>
      <strong>{{ taskResult?.score.totalScore ?? '—' }}<small v-if="taskResult?.score.totalScore !== null && taskResult?.score.totalScore !== undefined">分</small></strong>
      <StatusBadge :tone="taskResult?.score.totalScore !== null && taskResult?.score.totalScore !== undefined ? 'green' : 'gray'" :text="taskResult?.score.totalScore !== null && taskResult?.score.totalScore !== undefined ? '已评分' : '未评分'" />
    </div>
    <div class="card explanation">
      <div class="section-head">
        <div><div class="card-title">评分结果说明</div><div class="card-subtitle">未来由评分模块返回，前端不计算分数</div></div>
        <StatusBadge tone="orange" text="模型待接入" />
      </div>
      <ul v-if="taskResult?.score.explanation.length"><li v-for="item in taskResult.score.explanation" :key="item">{{ item }}</li></ul>
      <div v-else class="result-empty-copy">当前移动记录没有关联评分说明。</div>
      <div class="model">模型版本：{{ taskResult?.score.modelVersion ?? '待接入' }}</div>
    </div>
  </section>
</template>

<style scoped>
.result-info--review{grid-template-columns:repeat(5,minmax(0,1fr))}.result-info--review b{overflow-wrap:anywhere}.result-source-note{padding:14px 18px;display:flex;align-items:center;gap:10px;color:#68758b;font-size:12px}.result-empty-copy{min-height:120px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:7px;color:#7b879a;text-align:center}@media(max-width:1350px){.result-info--review{grid-template-columns:repeat(3,1fr)}}@media(max-width:680px){.result-info--review{grid-template-columns:1fr}.result-source-note{align-items:flex-start;flex-direction:column}}
</style>
